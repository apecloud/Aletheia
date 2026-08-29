import os
import sys
import time
import logging
import threading
import traceback
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from nebula3.gclient.net import ConnectionPool
from nebula3.Config import Config

logger = logging.getLogger("GraphDBClient")


def dump_all_thread_stacks(reason: str) -> str:
    """Forensic snapshot for a lock-acquire timeout: every live thread's
    current stack, so the NEXT hang tells us exactly which call is stuck
    (session.execute? a socket read? something else entirely) instead of
    just "something, somewhere, didn't return in time." Cheap enough to
    call unconditionally on the (rare) timeout path -- never call this on
    a hot path."""
    lines = [f"=== thread stack dump ({reason}) ==="]
    frames = sys._current_frames()
    names = {t.ident: t.name for t in threading.enumerate()}
    for thread_id, frame in frames.items():
        lines.append(f"--- thread {names.get(thread_id, '?')} (id={thread_id}) ---")
        lines.append("".join(traceback.format_stack(frame)))
    return "\n".join(lines)


def _is_nebula_session_invalid_error(message) -> bool:
    """True if a Nebula error/exception looks like the shared session was
    invalidated server-side (idle timeout, graphd restart) rather than a
    transient "space not ready yet" or query-syntax failure. Session errors
    never resolve by retrying the same session -- only a fresh connect()
    fixes them, so this distinction decides whether to reconnect or just
    keep polling."""
    text = str(message or "").lower()
    return "session not existed" in text or "session not found" in text or "sessionnotfound" in text or "session expired" in text or "session had been expired" in text


def insert_with_schema_retry(insert_call, *, retries: int = 3, retry_sleep: float = 5.0) -> None:
    """Retry an insert_vertices/insert_edges call if graphd's schema cache
    hasn't caught up yet ("No schema found" right after CREATE TAG/EDGE)."""
    for attempt in range(retries + 1):
        try:
            insert_call()
            return
        except Exception as exc:
            if "No schema found" not in str(exc) or attempt == retries:
                raise
            time.sleep(retry_sleep)


class BaseGraphClient(ABC):
    @abstractmethod
    def connect(self):
        """Establish connection to the graph database."""
        pass

    @abstractmethod
    def close(self):
        """Close connection."""
        pass

    @abstractmethod
    def execute_query(self, query: str) -> Any:
        """Execute a raw query/schema change."""
        pass

    @abstractmethod
    def insert_vertices(self, label: str, rows: List[Dict[str, Any]], batch_size: int = 100):
        """
        Insert vertices in batches.
        rows must contain 'id' as the primary identifier.
        """
        pass

    @abstractmethod
    def insert_edges(self, edge_type: str, rows: List[Dict[str, Any]], batch_size: int = 100):
        """
        Insert edges in batches.
        rows must contain 'source_id' and 'target_id'.
        """
        pass


class NebulaGraphClient(BaseGraphClient):
    """
    Client for Nebula Graph.
    Encapsulates connection, space selection, and safe batch ingestion
    to prevent timeouts and utf-8 decoding issues.
    """
    def __init__(self, ip: str, port: int, user: str, password: str, space: str = None):
        self.ip = ip
        self.port = port
        self.user = user
        self.password = password
        self.space = space or os.environ.get("ALETHEIA_GRAPH_SPACE", "aletheia")
        self.pool = None
        self.session = None
        # One shared nebula3-python Session per client instance (obtained
        # once in connect(), reused for every execute_query() call) -- the
        # server runs under ThreadingHTTPServer, so concurrent requests for
        # the same tenant would otherwise call session.execute() from
        # multiple threads at once. Nebula sessions are single-request-at-a
        # -time: concurrent use doesn't just race, it can desync the
        # underlying Thrift connection's request/response framing and leave
        # the session permanently returning errors for the rest of the
        # process's life (observed directly: Graph Explorer's parallel
        # on-load fetches broke a tenant's session, and every query kept
        # failing until the server was restarted). Serializing here trades
        # a little query throughput for a session that never corrupts.
        self._lock = threading.Lock()

    def connect(self):
        config = Config()
        config.max_connection_pool_size = 10
        # nebula3-python defaults to timeout=0 ("never times out"). Combined
        # with execute_query()'s lock (serializing all queries through one
        # shared session), a single call stuck on a dead/half-broken socket
        # would otherwise block that lock forever -- every subsequent
        # request for this tenant queues up behind it with no way out short
        # of restarting the process (observed directly: one hung request
        # froze the tenant for 10+ minutes with nothing showing in Nebula's
        # own `SHOW QUERIES`, i.e. the hang was in the client socket wait,
        # not server-side work). A finite timeout turns that failure mode
        # into "this one query raises", which the existing try/except
        # wrappers already handle, instead of "everything hangs forever".
        config.timeout = 15000
        self.pool = ConnectionPool()
        try:
            self.pool.init([(self.ip, self.port)], config)
            self.session = self.pool.get_session(self.user, self.password)
            logger.info(f"✅ Successfully connected to Nebula Graph at {self.ip}:{self.port}")
            
            # Setup cluster and space -- idempotent, so safe to run on every
            # connect() even when the host/space already exist (true for
            # every call after the cluster's first-ever bootstrap). No
            # blocking sleep after these: execute_query() already retries
            # `USE {space}` once with its own 2s backoff if propagation
            # genuinely hasn't caught up yet, so a fixed 15s tax on every
            # single connect() call was pure waste in the common case.
            self.session.execute('ADD HOSTS "storaged0":9779;')
            self.session.execute(f'CREATE SPACE IF NOT EXISTS {self.space} (partition_num=1, replica_factor=1, vid_type=FIXED_STRING(128));')

            use_res = self.session.execute(f'USE {self.space};')
            if not use_res.is_succeeded():
                logger.warning(f"Initial USE {self.space} failed: {use_res.error_msg()}. Will retry in execute_query.")
                
        except Exception as e:
            logger.error(f"❌ Failed to connect to Nebula Graph: {e}")
            raise

    def close(self):
        if self.session is not None:
            self.session.release()
        if self.pool is not None:
            self.pool.close()

    def execute_query(self, query: str):
        # Always ensure we are in the correct space. Polls with a bounded
        # retry loop instead of a single fixed sleep: a space that already
        # existed when connect() ran (true for every call after a cluster's
        # first-ever bootstrap) succeeds on the first try, so most callers
        # never wait at all -- only a genuinely brand-new space (still
        # propagating through Nebula's meta service right after CREATE
        # SPACE) pays the retry cost, and only for as long as it actually
        # takes to become ready, not a fixed worst-case sleep every time.
        # Bounded wait to acquire the lock, not an unconditional block: if
        # some earlier call is stuck inside session.execute() past its
        # configured socket timeout (observed directly -- a call outlived
        # even a 15s/40s wait with nothing showing in Nebula's own `SHOW
        # QUERIES`, i.e. stuck client-side, not server-side), an
        # unconditional `with self._lock` would queue every other caller
        # behind it forever too. Failing fast here contains the damage to
        # the one genuinely stuck request instead of freezing the whole
        # tenant.
        if not self._lock.acquire(timeout=20):
            logger.error(dump_all_thread_stacks(f"Nebula lock timeout on space {self.space!r}"))
            raise Exception(
                f"Nebula client for space {self.space!r} is busy (a prior query "
                "has not released the shared session after 20s) -- not waiting further."
            )
        try:
            return self._execute_query_locked(query, allow_reconnect=True)
        finally:
            self._lock.release()

    def _run_nebula(self, nql: str):
        """self.session.execute(), normalized so a raised exception (e.g. a
        broken socket) and a returned-but-failed result are handled the same
        way by the caller instead of needing two separate error paths."""
        try:
            return self.session.execute(nql), None
        except Exception as exc:
            return None, exc

    def _execute_query_locked(self, query: str, *, allow_reconnect: bool):
        # Always ensure we are in the correct space. Polls with a bounded
        # retry loop instead of a single fixed sleep: a space that already
        # existed when connect() ran (true for every call after a cluster's
        # first-ever bootstrap) succeeds on the first try, so most callers
        # never wait at all -- only a genuinely brand-new space (still
        # propagating through Nebula's meta service right after CREATE
        # SPACE) pays the retry cost, and only for as long as it actually
        # takes to become ready, not a fixed worst-case sleep every time.
        use_res, use_exc = self._run_nebula(f'USE {self.space};')
        attempt = 0
        while (use_exc is not None or not use_res.is_succeeded()) and attempt < 6:
            error_text = str(use_exc) if use_exc is not None else use_res.error_msg()
            if _is_nebula_session_invalid_error(error_text):
                # Retrying against the same dead session can never succeed --
                # break out early instead of burning the whole 6*2s budget.
                break
            time.sleep(2)
            use_res, use_exc = self._run_nebula(f'USE {self.space};')
            attempt += 1

        if use_exc is not None or not use_res.is_succeeded():
            error_text = str(use_exc) if use_exc is not None else use_res.error_msg()
            if allow_reconnect and _is_nebula_session_invalid_error(error_text):
                # The shared session outlived Nebula's idle timeout (observed
                # directly: a long-running server process's session silently
                # invalidated server-side, and every subsequent query kept
                # retrying against the now-nonexistent session id forever).
                # One reconnect gets a fresh session; allow_reconnect=False
                # on the retry bounds this to a single attempt so a
                # genuinely broken cluster still fails instead of looping.
                logger.warning(f"Nebula session for space {self.space!r} expired ({error_text}); reconnecting.")
                self.connect()
                return self._execute_query_locked(query, allow_reconnect=False)
            raise Exception(f"USE {self.space} failed after retries: {error_text}")

        logger.debug(f"Executing nGQL: {query[:100]}...") # Truncated for clean logs
        result, result_exc = self._run_nebula(query)
        if result_exc is not None or not result.is_succeeded():
            error_text = str(result_exc) if result_exc is not None else result.error_msg()
            if allow_reconnect and _is_nebula_session_invalid_error(error_text):
                logger.warning(f"Nebula session for space {self.space!r} expired mid-query ({error_text}); reconnecting.")
                self.connect()
                return self._execute_query_locked(query, allow_reconnect=False)
            logger.error(f"nGQL Error: {error_text} for query: {query[:200]}")
            raise Exception(error_text)
        return result

    def insert_vertices(self, label: str, rows: List[Dict[str, Any]], batch_size: int = 100):
        if not rows:
            return
            
        props_keys = [k for k in rows[0].keys() if k != 'id']
        insert_head = f"INSERT VERTEX `{label}` ({','.join(props_keys)}) VALUES "
        
        values_list = []
        for row in rows:
            # Safe string conversion to prevent utf-8 errors
            vid = str(row['id']).replace('"', "'")
            vals = []
            for k in props_keys:
                val = str(row[k]).replace('"', "'") if row[k] is not None else ""
                vals.append(f'"{val}"')
            values_list.append(f'"{vid}": ({",".join(vals)})')
        
        # Batch insert to prevent socket timed out
        for i in range(0, len(values_list), batch_size):
            batch = values_list[i:i+batch_size]
            ngql_insert = insert_head + ", ".join(batch) + ";"
            self.execute_query(ngql_insert)
            
    def insert_edges(self, edge_type: str, rows: List[Dict[str, Any]], batch_size: int = 100):
        if not rows:
            return
            
        props_keys = [k for k in rows[0].keys() if k not in ('source_id', 'target_id')]
        
        if props_keys:
            insert_head = f"INSERT EDGE `{edge_type}` ({','.join(props_keys)}) VALUES "
        else:
            insert_head = f"INSERT EDGE `{edge_type}` () VALUES "
            
        values_list = []
        for row in rows:
            # Safe string conversion
            src = str(row['source_id']).replace('"', "'")
            tgt = str(row['target_id']).replace('"', "'")
            
            if props_keys:
                vals = []
                for k in props_keys:
                    val = str(row[k]).replace('"', "'") if row[k] is not None else ""
                    vals.append(f'"{val}"')
                values_list.append(f'"{src}"->"{tgt}": ({",".join(vals)})')
            else:
                values_list.append(f'"{src}"->"{tgt}": ()')
                
        # Batch insert
        for i in range(0, len(values_list), batch_size):
            batch = values_list[i:i+batch_size]
            ngql_insert = insert_head + ", ".join(batch) + ";"
            self.execute_query(ngql_insert)
