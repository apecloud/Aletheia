import time
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from nebula3.gclient.net import ConnectionPool
from nebula3.Config import Config

logger = logging.getLogger("GraphDBClient")

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
    def __init__(self, ip: str, port: int, user: str, password: str, space: str = "aletheia"):
        self.ip = ip
        self.port = port
        self.user = user
        self.password = password
        self.space = space
        self.pool = None
        self.session = None

    def connect(self):
        config = Config()
        config.max_connection_pool_size = 10
        self.pool = ConnectionPool()
        try:
            self.pool.init([(self.ip, self.port)], config)
            self.session = self.pool.get_session(self.user, self.password)
            logger.info(f"✅ Successfully connected to Nebula Graph at {self.ip}:{self.port}")
            
            # Setup cluster and space
            self.session.execute('ADD HOSTS "storaged0":9779;')
            time.sleep(5)
            self.session.execute(f'CREATE SPACE IF NOT EXISTS {self.space} (partition_num=1, replica_factor=1, vid_type=FIXED_STRING(128));')
            time.sleep(10)  # Nebula needs a moment after creating space
            
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
        # Always ensure we are in the correct space
        use_res = self.session.execute(f'USE {self.space};')
        if not use_res.is_succeeded():
            time.sleep(2)
            self.session.execute(f'USE {self.space};')
            
        logger.debug(f"Executing nGQL: {query[:100]}...") # Truncated for clean logs
        result = self.session.execute(query)
        if not result.is_succeeded():
            logger.error(f"nGQL Error: {result.error_msg()} for query: {query[:200]}")
            raise Exception(result.error_msg())
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
