"""
Aletheia Universal Reasoning Engine
====================================
Schema-agnostic deep analysis for any entity type. Discovers table structures,
column types, FK relationships, and aggregatable dimensions at runtime from
approved schema-graph projection metadata, SQL introspection, and ontology
artifact descriptions. Legacy ENTITY_CONFIG/LINK_CONFIG fixtures are repository
fallbacks only when approved SchemaGraphModelingAgent projection metadata is not
available.

Usage:
    from reasoning_engine import ReasoningEngine
    engine = ReasoningEngine(instance_repository)
    result = engine.analyze(tenant, "Employee:1", "Is Nancy a top performer?")
"""

import json
from datetime import datetime
from sqlalchemy import inspect, text


def _jsonable(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _fmt_number(v):
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return f"{v:,.2f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def _quote_ident(name):
    return "`" + str(name).replace("`", "``") + "`"


class TableMeta:
    __slots__ = ("table", "pk_col", "fk_cols", "date_cols", "numeric_cols", "text_cols", "all_cols")

    def __init__(self, table, pk_col, fk_cols, date_cols, numeric_cols, text_cols, all_cols):
        self.table = table
        self.pk_col = pk_col
        self.fk_cols = fk_cols
        self.date_cols = date_cols
        self.numeric_cols = numeric_cols
        self.text_cols = text_cols
        self.all_cols = all_cols


class ReasoningEngine:

    def __init__(self, instance_repository):
        self.repo = instance_repository
        self._table_meta_cache = {}

    def _entity_config(self, tenant):
        if hasattr(self.repo, "reasoning_entity_config"):
            return self.repo.reasoning_entity_config(tenant)
        return getattr(self.repo, "ENTITY_CONFIG")

    def _link_config(self, tenant):
        if hasattr(self.repo, "reasoning_link_config"):
            return self.repo.reasoning_link_config(tenant)
        return getattr(self.repo, "LINK_CONFIG")

    # ------------------------------------------------------------------
    # Step 1: Schema introspection
    # ------------------------------------------------------------------

    def _introspect_table(self, engine, table_name, tenant=None):
        cache_key = f"{id(engine)}:{table_name}"
        if cache_key in self._table_meta_cache:
            return self._table_meta_cache[cache_key]

        with engine.connect() as conn:
            rows = conn.execute(text(f"DESCRIBE `{table_name}`")).fetchall()

        pk_col = None
        fk_cols = set()
        date_cols = []
        numeric_cols = []
        text_cols = []
        all_cols = []

        for row in rows:
            col_name = row[0]
            col_type = (row[1] or "").lower()
            col_key = (row[3] or "").upper() if len(row) > 3 else ""
            all_cols.append(col_name)

            if col_key == "PRI":
                pk_col = col_name

            col_lower = col_name.lower()
            if "date" in col_type or "time" in col_type:
                date_cols.append(col_name)
            elif any(t in col_type for t in ("int", "float", "double", "decimal", "numeric")):
                numeric_cols.append(col_name)
            elif any(t in col_type for t in ("char", "text", "varchar")):
                # Heuristic: text columns named *date* or *time* are likely dates
                if any(hint in col_lower for hint in ("date", "_at", "_time", "timestamp")):
                    date_cols.append(col_name)
                else:
                    text_cols.append(col_name)

        for lc in self._link_config(tenant) if tenant is not None else getattr(self.repo, "LINK_CONFIG"):
            if lc["fk_table"] == table_name:
                fk_cols.add(lc["fk_col"])
                if lc.get("target_fk"):
                    fk_cols.add(lc["target_fk"])

        for cfg in (self._entity_config(tenant) if tenant is not None else getattr(self.repo, "ENTITY_CONFIG")).values():
            if cfg["table"] == table_name:
                fk_cols.add(cfg["pk"])

        meta = TableMeta(
            table=table_name,
            pk_col=pk_col,
            fk_cols=fk_cols,
            date_cols=date_cols,
            numeric_cols=[c for c in numeric_cols if c not in fk_cols and c != pk_col],
            text_cols=text_cols,
            all_cols=all_cols,
        )
        self._table_meta_cache[cache_key] = meta
        return meta

    # ------------------------------------------------------------------
    # Step 2: Artifact descriptions
    # ------------------------------------------------------------------

    def _artifact_descriptions(self, tenant, keys):
        arts = self.repo._approved_artifacts(tenant, keys)
        return {k: v.get("description") or "" for k, v in arts.items()}

    # ------------------------------------------------------------------
    # Step 3: Format entity properties
    # ------------------------------------------------------------------

    def _format_properties(self, row, cfg, source_engine, tenant=None):
        meta = self._introspect_table(source_engine, cfg["table"], tenant=tenant)
        skip = meta.fk_cols | {meta.pk_col}
        import re
        _date_re = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
        props = []
        for k, v in row.items():
            if k in skip or v is None:
                continue
            v_str = str(_jsonable(v))
            if not v_str.strip() or len(v_str) > 200:
                continue
            if hasattr(v, "strftime"):
                v_str = v.strftime("%Y-%m-%d")
            elif _date_re.match(v_str):
                v_str = v_str[:10]
            props.append({"col": k, "value": v_str})
        return props

    def _source_key_profile(self, tenant, object_type, instance_id, cfg, depth=1):
        """Aggregate source tables that share the center entity key.

        This intentionally uses schema evidence (shared key columns, numeric
        columns, and candidate label columns) instead of domain-coded graph
        relation names, so it can improve sparse graph profiles without
        minting ontology or graph semantics.
        """
        source_engine = self.repo.source_engine_for(tenant)
        center_table = cfg.get("table")
        key_col = cfg.get("pk")
        if not center_table or not key_col:
            return None
        inspector = inspect(source_engine)
        try:
            table_names = inspector.get_table_names()
        except Exception:
            return None

        allowed_tables = set()
        try:
            with self.repo.metadata_engine_for(tenant).connect() as conn:
                artifact_rows = conn.execute(
                    text(
                        """
                        SELECT source_refs_json, payload_json
                        FROM aletheia_ontology_artifacts
                        WHERE project_id = :tenant_id AND status = 'approved'
                        """
                    ),
                    {"tenant_id": tenant.tenant_id},
                ).mappings().all()
            for artifact in artifact_rows:
                source_refs = artifact.get("source_refs_json") or []
                if isinstance(source_refs, str):
                    source_refs = json.loads(source_refs or "[]")
                for ref in source_refs or []:
                    if isinstance(ref, str) and ref.startswith("table:"):
                        allowed_tables.add(ref.removeprefix("table:"))

                payload = artifact.get("payload_json") or {}
                if isinstance(payload, str):
                    payload = json.loads(payload or "{}")
                for table in payload.get("mapped_table_names") or []:
                    allowed_tables.add(table)
                for key in ("source_table", "target_table"):
                    if payload.get(key):
                        allowed_tables.add(payload[key])
        except Exception:
            allowed_tables = set()
        if allowed_tables:
            table_names = [table for table in table_names if table in allowed_tables]

        related_tables = []
        total_rows = 0
        numeric_totals = {}
        label_candidates = ("canal", "chokepoint", "strait", "route", "corridor", "port", "location")
        risk_metric_hints = ("risk", "impacted", "impact", "at_risk", "share", "dependency", "v_canal", "q_canal", "revenue")
        top_paths = []
        second_hop_paths = []

        with source_engine.connect() as conn:
            for table in table_names:
                try:
                    meta = self._introspect_table(source_engine, table, tenant=tenant)
                except Exception:
                    continue
                if key_col not in meta.all_cols:
                    continue
                qt = _quote_ident(table)
                qk = _quote_ident(key_col)
                try:
                    row_count = int(conn.execute(
                        text(f"SELECT COUNT(*) FROM {qt} WHERE {qk} = :id"),
                        {"id": instance_id},
                    ).scalar() or 0)
                except Exception:
                    continue
                if row_count <= 0:
                    continue

                total_rows += row_count
                label_col = next((c for c in meta.all_cols if c.lower() in label_candidates), None)
                distinct_labels = 0
                if label_col:
                    try:
                        distinct_labels = int(conn.execute(
                            text(f"SELECT COUNT(DISTINCT {_quote_ident(label_col)}) FROM {qt} WHERE {qk} = :id AND {_quote_ident(label_col)} IS NOT NULL"),
                            {"id": instance_id},
                        ).scalar() or 0)
                    except Exception:
                        distinct_labels = 0

                metric_cols = [
                    c for c in meta.numeric_cols
                    if c != key_col and any(hint in c.lower() for hint in risk_metric_hints)
                ][:8]
                table_totals = {}
                if metric_cols:
                    expr = ", ".join(f"COALESCE(SUM({_quote_ident(c)}), 0) AS {_quote_ident('sum_' + c)}" for c in metric_cols)
                    try:
                        sums = conn.execute(
                            text(f"SELECT {expr} FROM {qt} WHERE {qk} = :id"),
                            {"id": instance_id},
                        ).mappings().first()
                    except Exception:
                        sums = None
                    if sums:
                        for col in metric_cols:
                            val = float(sums.get("sum_" + col) or 0)
                            table_totals[col] = val
                            numeric_totals[col] = numeric_totals.get(col, 0.0) + val

                if label_col and metric_cols:
                    preferred = next((c for c in metric_cols if "at_risk" in c.lower()), None)
                    preferred = preferred or next((c for c in metric_cols if "impacted" in c.lower()), None)
                    preferred = preferred or next((c for c in metric_cols if "v_canal" in c.lower()), None)
                    preferred = preferred or metric_cols[0]
                    try:
                        top_rows = conn.execute(
                            text(
                                f"SELECT {_quote_ident(label_col)} AS label, "
                                f"COUNT(*) AS cnt, COALESCE(SUM({_quote_ident(preferred)}), 0) AS metric "
                                f"FROM {qt} WHERE {qk} = :id AND {_quote_ident(label_col)} IS NOT NULL "
                                f"GROUP BY {_quote_ident(label_col)} ORDER BY metric DESC LIMIT 5"
                            ),
                            {"id": instance_id},
                        ).mappings().all()
                    except Exception:
                        top_rows = []
                    for top in top_rows:
                        label_value = str(top["label"])
                        top_paths.append({
                            "table": table,
                            "label_col": label_col,
                            "label": label_value,
                            "metric": preferred,
                            "metric_value": float(top["metric"] or 0),
                            "row_count": int(top["cnt"] or 0),
                        })
                        if int(depth or 1) >= 2:
                            try:
                                peer_rows = conn.execute(
                                    text(
                                        f"SELECT {qk} AS peer_key, "
                                        f"COUNT(*) AS cnt, COALESCE(SUM({_quote_ident(preferred)}), 0) AS metric "
                                        f"FROM {qt} "
                                        f"WHERE {_quote_ident(label_col)} = :label AND {qk} != :id "
                                        f"GROUP BY {qk} ORDER BY metric DESC LIMIT 12"
                                    ),
                                    {"label": label_value, "id": instance_id},
                                ).mappings().all()
                            except Exception:
                                peer_rows = []
                            if peer_rows:
                                second_hop_paths.append({
                                    "table": table,
                                    "label_col": label_col,
                                    "label": label_value,
                                    "metric": preferred,
                                    "metric_value": float(top["metric"] or 0),
                                    "peer_count": len(peer_rows),
                                    "top_peers": [
                                        {
                                            "key": str(peer["peer_key"]),
                                            "metric_value": float(peer["metric"] or 0),
                                            "row_count": int(peer["cnt"] or 0),
                                        }
                                        for peer in peer_rows
                                    ],
                                })

                related_tables.append({
                    "table": table,
                    "key_col": key_col,
                    "row_count": row_count,
                    "label_col": label_col,
                    "distinct_labels": distinct_labels,
                    "metric_totals": table_totals,
                })

        if not related_tables:
            return None
        top_paths.sort(key=lambda item: item.get("metric_value", 0), reverse=True)
        second_hop_paths.sort(key=lambda item: item.get("metric_value", 0), reverse=True)
        return {
            "center_key_col": key_col,
            "scope_depth": int(depth or 1),
            "related_tables": related_tables,
            "total_key_rows": total_rows,
            "numeric_totals": numeric_totals,
            "top_paths": top_paths[:8],
            "second_hop_paths": second_hop_paths[:8],
        }

    # ------------------------------------------------------------------
    # Step 4: Peer ranking (unchanged logic, moved here)
    # ------------------------------------------------------------------

    def _peer_rankings(self, tenant, object_type, instance_id):
        entity_config = self._entity_config(tenant)
        link_config = self._link_config(tenant)
        cfg = entity_config.get(object_type.lower())
        if not cfg:
            return []
        rankings = []
        source_engine = self.repo.source_engine_for(tenant)
        with source_engine.connect() as conn:
            for lc in link_config:
                if lc["from"] != object_type.lower() or lc.get("reverse"):
                    continue
                fk_table, fk_col = lc["fk_table"], lc["fk_col"]
                try:
                    rows = conn.execute(text(
                        f"SELECT `{fk_col}` AS fk, COUNT(*) AS cnt "
                        f"FROM `{fk_table}` WHERE `{fk_col}` IS NOT NULL "
                        f"GROUP BY `{fk_col}` ORDER BY cnt DESC"
                    )).mappings().all()
                except Exception:
                    continue
                if not rows:
                    continue
                counts = {str(r["fk"]): int(r["cnt"]) for r in rows}
                total_peers = len(counts)
                my_count = counts.get(str(instance_id), 0)
                sorted_counts = sorted(counts.values(), reverse=True)
                rank = sorted_counts.index(my_count) + 1 if my_count in sorted_counts else total_peers
                avg_count = sum(sorted_counts) / total_peers if total_peers else 0
                max_count = sorted_counts[0] if sorted_counts else 0
                percentile = round((total_peers - rank) / max(total_peers - 1, 1) * 100) if total_peers > 1 else 100
                level = "high" if percentile >= 75 else ("average" if percentile >= 40 else "low")
                rankings.append({
                    "link": lc["link"],
                    "target_type": lc["to"],
                    "fk_table": fk_table,
                    "fk_col": fk_col,
                    "my_count": my_count,
                    "rank": rank,
                    "total_peers": total_peers,
                    "percentile": percentile,
                    "avg": round(avg_count, 1),
                    "max": max_count,
                    "level": level,
                })
        return rankings

    # ------------------------------------------------------------------
    # Step 5: Per-link deep aggregation
    # ------------------------------------------------------------------

    def _link_deep_stats(self, tenant, object_type, instance_id, lc, ranking):
        """For one link, compute numeric stats, date range, top counterparties, time bucketing."""
        source_engine = self.repo.source_engine_for(tenant)
        entity_config = self._entity_config(tenant)
        link_config = self._link_config(tenant)
        meta = self._introspect_table(source_engine, lc["fk_table"], tenant=tenant)
        fk_col = lc["fk_col"]
        result = {"link": lc["link"], "target_type": lc["to"]}

        with source_engine.connect() as conn:
            # Numeric column aggregation
            agg_cols = [c for c in meta.numeric_cols if c != fk_col]
            if agg_cols:
                agg_exprs = []
                for c in agg_cols:
                    agg_exprs.append(f"COALESCE(SUM(`{c}`), 0) AS `sum_{c}`")
                    agg_exprs.append(f"COALESCE(AVG(`{c}`), 0) AS `avg_{c}`")
                sql = (
                    f"SELECT COUNT(*) AS cnt, {', '.join(agg_exprs)} "
                    f"FROM `{meta.table}` WHERE `{fk_col}` = :id"
                )
                try:
                    row = conn.execute(text(sql), {"id": instance_id}).mappings().first()
                    if row:
                        numeric_stats = {}
                        for c in agg_cols:
                            numeric_stats[c] = {
                                "sum": float(row[f"sum_{c}"] or 0),
                                "avg": float(row[f"avg_{c}"] or 0),
                            }
                        result["numeric_stats"] = numeric_stats
                        result["row_count"] = int(row["cnt"] or 0)
                except Exception:
                    pass

            # Date range
            if meta.date_cols:
                date_col = meta.date_cols[0]
                try:
                    row = conn.execute(text(
                        f"SELECT MIN(`{date_col}`) AS d_min, MAX(`{date_col}`) AS d_max "
                        f"FROM `{meta.table}` WHERE `{fk_col}` = :id"
                    ), {"id": instance_id}).mappings().first()
                    if row and row["d_min"]:
                        result["date_range"] = {
                            "col": date_col,
                            "min": str(_jsonable(row["d_min"]))[:10],
                            "max": str(_jsonable(row["d_max"]))[:10],
                        }
                except Exception:
                    pass

            # Top-N counterparties (other FK columns on the same table)
            other_fks = []
            for other_lc in link_config:
                if other_lc["fk_table"] == meta.table and other_lc["fk_col"] != fk_col:
                    other_fks.append((other_lc["fk_col"], other_lc["to"] if other_lc["from"] == lc["to"] else other_lc["from"]))
            if lc.get("target_fk"):
                other_fks.append((lc["target_fk"], lc["to"]))

            for other_fk, other_type in other_fks[:2]:
                other_cfg = entity_config.get(other_type.lower())
                try:
                    top_rows = conn.execute(text(
                        f"SELECT `{other_fk}` AS fk_val, COUNT(*) AS cnt "
                        f"FROM `{meta.table}` WHERE `{fk_col}` = :id AND `{other_fk}` IS NOT NULL "
                        f"GROUP BY `{other_fk}` ORDER BY cnt DESC LIMIT 5"
                    ), {"id": instance_id}).mappings().all()
                    if top_rows:
                        top_list = []
                        for tr in top_rows:
                            label = str(tr["fk_val"])
                            if other_cfg:
                                try:
                                    entity_row = self.repo._fetch_entity(tenant, other_type, str(tr["fk_val"]))
                                    if entity_row:
                                        node = self.repo._entity_node(tenant, other_type, entity_row)
                                        label = node.get("label", label) if node else label
                                except Exception:
                                    pass
                            top_list.append({"id": str(tr["fk_val"]), "label": label, "count": int(tr["cnt"])})
                        result.setdefault("top_counterparties", {})[other_type] = {
                            "fk_col": other_fk,
                            "items": top_list,
                            "total_distinct": len(top_rows),
                        }
                except Exception:
                    pass

            # Count distinct counterparties
            for other_fk, other_type in other_fks[:2]:
                try:
                    cnt = conn.execute(text(
                        f"SELECT COUNT(DISTINCT `{other_fk}`) AS cnt "
                        f"FROM `{meta.table}` WHERE `{fk_col}` = :id AND `{other_fk}` IS NOT NULL"
                    ), {"id": instance_id}).scalar()
                    result.setdefault("distinct_counterparties", {})[other_type] = int(cnt or 0)
                except Exception:
                    pass

            # Time bucketing
            if meta.date_cols:
                date_col = meta.date_cols[0]
                try:
                    yearly = conn.execute(text(
                        f"SELECT YEAR(`{date_col}`) AS yr, COUNT(*) AS cnt "
                        f"FROM `{meta.table}` WHERE `{fk_col}` = :id AND `{date_col}` IS NOT NULL "
                        f"GROUP BY YEAR(`{date_col}`) ORDER BY yr"
                    ), {"id": instance_id}).mappings().all()
                    if yearly:
                        result["yearly"] = [{"year": int(r["yr"]), "count": int(r["cnt"])} for r in yearly if r["yr"]]
                except Exception:
                    pass

        return result

    # ------------------------------------------------------------------
    # Step 6: Multi-hop value aggregation (n:m detail tables)
    # ------------------------------------------------------------------

    def _multihop_value(self, tenant, object_type, instance_id):
        """For n:m links via detail tables, compute value aggregation and category breakdown.

        Discovers both direct paths (entity has n:m link with target_fk) and
        indirect paths (entity → 1:n → intermediary → n:m via detail table).
        """
        source_engine = self.repo.source_engine_for(tenant)
        results = []

        # Collect (detail_lc, inter_table, inter_pk, inter_fk) tuples to process
        chains = []
        entity_config = self._entity_config(tenant)
        link_config = self._link_config(tenant)

        for lc in link_config:
            if not lc.get("target_fk"):
                continue

            if lc["from"] == object_type.lower():
                # Direct n:m from this entity type — find the intermediary
                detail_table = lc["fk_table"]
                fk_col = lc["fk_col"]
                for olc in link_config:
                    if olc["from"] == object_type.lower() and olc["to"] != lc["to"]:
                        inter_cfg = entity_config.get(olc["to"])
                        if inter_cfg and inter_cfg["pk"] == fk_col:
                            chains.append((lc, inter_cfg["table"], inter_cfg["pk"], olc["fk_col"]))
                            break
            else:
                # Indirect: entity → 1:n → intermediary, and intermediary has the n:m link
                inter_type = lc["from"]
                for bridge_lc in link_config:
                    if bridge_lc["from"] == object_type.lower() and bridge_lc["to"] == inter_type and not bridge_lc.get("reverse"):
                        inter_cfg = entity_config.get(inter_type)
                        if inter_cfg:
                            chains.append((lc, inter_cfg["table"], inter_cfg["pk"], bridge_lc["fk_col"]))
                            break

        for lc, inter_table, inter_pk, inter_fk in chains:
            detail_table = lc["fk_table"]
            fk_col = lc["fk_col"]
            target_fk = lc["target_fk"]
            meta = self._introspect_table(source_engine, detail_table, tenant=tenant)

            value_cols = [c for c in meta.numeric_cols if c not in meta.fk_cols]
            if len(value_cols) < 2:
                continue

            col_a, col_b = value_cols[0], value_cols[1]
            discount_col = value_cols[2] if len(value_cols) > 2 else None

            if discount_col:
                val_expr = f"COALESCE(SUM(d.`{col_a}` * d.`{col_b}` * (1 - d.`{discount_col}`)), 0)"
            else:
                val_expr = f"COALESCE(SUM(d.`{col_a}` * d.`{col_b}`), 0)"

            with source_engine.connect() as conn:
                try:
                    my_value = conn.execute(text(
                        f"SELECT {val_expr} AS val "
                        f"FROM `{inter_table}` i "
                        f"JOIN `{detail_table}` d ON d.`{fk_col}` = i.`{inter_pk}` "
                        f"WHERE i.`{inter_fk}` = :id"
                    ), {"id": instance_id}).scalar()
                    my_value = float(my_value or 0)
                except Exception:
                    continue

                try:
                    total_value = conn.execute(text(
                        f"SELECT {val_expr} AS val FROM `{detail_table}` d"
                    )).scalar()
                    total_value = float(total_value or 0)
                except Exception:
                    total_value = 0

                try:
                    peer_rows = conn.execute(text(
                        f"SELECT i.`{inter_fk}` AS fk, {val_expr} AS val "
                        f"FROM `{inter_table}` i "
                        f"JOIN `{detail_table}` d ON d.`{fk_col}` = i.`{inter_pk}` "
                        f"GROUP BY i.`{inter_fk}` ORDER BY val DESC"
                    )).mappings().all()
                    values_list = [float(r["val"]) for r in peer_rows]
                    value_rank = values_list.index(my_value) + 1 if my_value in values_list else len(values_list)
                    value_total_peers = len(values_list)
                except Exception:
                    value_rank = None
                    value_total_peers = None

                category_breakdown = []
                target_type = lc["to"]
                target_cfg = entity_config.get(target_type)
                if target_cfg:
                    for cat_lc in link_config:
                        if cat_lc["to"] == target_type and cat_lc["from"] != object_type.lower() and not cat_lc.get("target_fk"):
                            cat_type = cat_lc["from"]
                            cat_cfg = entity_config.get(cat_type)
                            if not cat_cfg:
                                continue
                            cat_fk = cat_lc["fk_col"]
                            try:
                                cat_rows = conn.execute(text(
                                    f"SELECT ct.`{cat_cfg['label_cols'][0]}` AS cat_label, "
                                    f"COUNT(DISTINCT i.`{inter_pk}`) AS cnt, "
                                    f"{val_expr} AS val "
                                    f"FROM `{inter_table}` i "
                                    f"JOIN `{detail_table}` d ON d.`{fk_col}` = i.`{inter_pk}` "
                                    f"JOIN `{target_cfg['table']}` t ON t.`{target_cfg['pk']}` = d.`{target_fk}` "
                                    f"JOIN `{cat_cfg['table']}` ct ON ct.`{cat_cfg['pk']}` = t.`{cat_fk}` "
                                    f"WHERE i.`{inter_fk}` = :id "
                                    f"GROUP BY ct.`{cat_cfg['label_cols'][0]}` "
                                    f"ORDER BY val DESC LIMIT 5"
                                ), {"id": instance_id}).mappings().all()
                                category_breakdown = [
                                    {"label": r["cat_label"], "count": int(r["cnt"]), "value": float(r["val"])}
                                    for r in cat_rows
                                ]
                            except Exception:
                                pass
                            break

                results.append({
                    "link": lc["link"],
                    "detail_table": detail_table,
                    "value_expr": f"{col_a} * {col_b}" + (f" * (1-{discount_col})" if discount_col else ""),
                    "my_value": my_value,
                    "total_value": total_value,
                    "value_share": my_value / total_value if total_value else 0,
                    "value_rank": value_rank,
                    "value_total_peers": value_total_peers,
                    "category_breakdown": category_breakdown,
                })

        return results

    # ------------------------------------------------------------------
    # Step 7: Self-referencing link resolution
    # ------------------------------------------------------------------

    def _resolve_self_refs(self, tenant, object_type, row, cfg):
        refs = {}
        for lc in self._link_config(tenant):
            if lc["from"] == object_type.lower() and lc["to"] == object_type.lower() and lc.get("reverse"):
                fk_col = lc["fk_col"]
                fk_val = row.get(fk_col)
                if fk_val:
                    parent = self.repo._fetch_entity(tenant, object_type, str(int(float(fk_val))))
                    if parent:
                        node = self.repo._entity_node(tenant, object_type, parent)
                        refs[fk_col] = {"id": str(int(float(fk_val))), "label": node.get("label", str(fk_val))}
        return refs

    # ------------------------------------------------------------------
    # Main entry: analyze
    # ------------------------------------------------------------------

    def analyze(self, tenant, center_node, question=None, depth=1, limit=200):
        if not center_node or ":" not in center_node:
            return None
        object_type, instance_id = center_node.split(":", 1)
        entity_config = self._entity_config(tenant)
        link_config = self._link_config(tenant)
        cfg = entity_config.get(object_type.lower())
        if not cfg:
            return None

        row = self.repo._fetch_entity(tenant, object_type, instance_id)
        if not row:
            return {
                "title": f"{center_node} profile unavailable",
                "profile_summary": f"{center_node} not found in the controlled data source.",
                "key_facts": [],
                "business_interpretation": ["Entity record missing — cannot perform analysis."],
                "evidence_limits": [f"Missing {object_type} source table record."],
                "next_questions": ["Verify entity ID exists in the current tenant data source."],
            }

        graph = self.repo.neighborhood(tenant, object_type, instance_id, depth=depth, limit=limit)
        if not graph or not graph.get("approved"):
            return None

        center = graph.get("center") or {}
        label = center.get("label") or center_node
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        source_engine = self.repo.source_engine_for(tenant)

        # --- Artifact descriptions ---
        desc_keys = [cfg.get("artifact", f"object:{object_type}")]
        for lc in link_config:
            if lc["from"] == object_type.lower() or lc["to"] == object_type.lower():
                desc_keys.append(lc["link"])
        descriptions = self._artifact_descriptions(tenant, desc_keys)
        entity_desc = descriptions.get(cfg.get("artifact", ""), "")

        # --- Self-referencing resolution ---
        self_refs = self._resolve_self_refs(tenant, object_type, row, cfg)

        # --- Properties ---
        props = self._format_properties(row, cfg, source_engine, tenant=tenant)

        # --- Peer rankings ---
        rankings = self._peer_rankings(tenant, object_type, instance_id)

        # --- Deep per-link stats ---
        link_stats = []
        for r in rankings:
            lc = next((l for l in link_config if l["link"] == r["link"]), None)
            if lc:
                stats = self._link_deep_stats(tenant, object_type, instance_id, lc, r)
                link_stats.append(stats)

        # --- Multi-hop value ---
        value_aggs = self._multihop_value(tenant, object_type, instance_id)

        # --- Source-key profile ---
        # Some tenants have reviewed graph projections whose semantics are
        # carried by source-table evidence rather than legacy LINK_CONFIG rows.
        # Use shared source keys to produce degree/metric evidence without
        # inventing graph writes or ontology terms.
        source_key_profile = None
        if not rankings:
            source_key_profile = self._source_key_profile(tenant, object_type, instance_id, cfg, depth=depth)

        # --- Neighbors by type (from graph) ---
        neighbors_by_type = {}
        for node in nodes:
            if node.get("id") == center.get("id"):
                continue
            ntype = node.get("type", "unknown")
            neighbors_by_type.setdefault(ntype, []).append(node)

        # --- Compose ---
        return self._compose(
            center_node=center_node,
            object_type=object_type,
            instance_id=instance_id,
            label=label,
            cfg=cfg,
            entity_desc=entity_desc,
            descriptions=descriptions,
            props=props,
            self_refs=self_refs,
            rankings=rankings,
            link_stats=link_stats,
            value_aggs=value_aggs,
            source_key_profile=source_key_profile,
            neighbors_by_type=neighbors_by_type,
            nodes=nodes,
            edges=edges,
            question=question,
        )

    # ------------------------------------------------------------------
    # Narrative builder
    # ------------------------------------------------------------------

    def _build_narrative(self, label, object_type, entity_desc, props, self_refs,
                         rankings, link_stats, value_aggs, neighbors_by_type, question,
                         source_key_profile=None):
        """Synthesize computed data into an analytical paragraph."""
        sentences = []

        # Identity sentence — role/title + context
        title_prop = next((p for p in props if p["col"].lower() in ("title", "contacttitle", "jobtitle", "role")), None)
        identity = f"{label}"
        if title_prop:
            identity += f" ({title_prop['value']})"
        if self_refs:
            ref = list(self_refs.values())[0]
            identity += f", reporting to {ref['label']}"

        # Activity level sentence
        if source_key_profile and source_key_profile.get("related_tables"):
            top_paths = source_key_profile.get("top_paths") or []
            second_hop_paths = source_key_profile.get("second_hop_paths") or []
            table_count = len(source_key_profile.get("related_tables") or [])
            row_count = source_key_profile.get("total_key_rows", 0)
            distinct_labels = sum(int(t.get("distinct_labels") or 0) for t in source_key_profile.get("related_tables") or [])
            if top_paths:
                top_text = ", ".join(
                    f"{p['label']} ({p['metric']} {_fmt_number(p['metric_value'])})"
                    for p in top_paths[:3]
                )
                sentences.append(
                    f"{identity} has {row_count} source rows across {table_count} related source table(s), "
                    f"covering {distinct_labels} distinct path labels. The largest current exposure paths are {top_text}."
                )
                if second_hop_paths:
                    peer_text = "; ".join(
                        f"{p['label']} also connects {', '.join(peer['key'] for peer in p.get('top_peers', [])[:4])}"
                        for p in second_hop_paths[:3]
                    )
                    sentences.append(
                        f"At depth {source_key_profile.get('scope_depth', 1)}, shared-path context adds peer countries through the same path labels: {peer_text}."
                    )
            else:
                sentences.append(
                    f"{identity} has {row_count} source rows across {table_count} related source table(s), "
                    f"with {distinct_labels} distinct connected path labels in the controlled source data."
                )
        elif rankings:
            r = rankings[0]
            ratio = r["my_count"] / r["avg"] if r["avg"] > 0 else 1
            if r["level"] == "high":
                sentences.append(
                    f"{identity} is a high-activity {object_type} with {r['my_count']} {r['target_type']}(s), "
                    f"ranking #{r['rank']} out of {r['total_peers']} peers — "
                    f"{ratio:.1f}x the average of {r['avg']}."
                )
            elif r["level"] == "low":
                sentences.append(
                    f"{identity} shows below-average activity with {r['my_count']} {r['target_type']}(s), "
                    f"ranking #{r['rank']} out of {r['total_peers']} peers "
                    f"(avg {r['avg']}, max {r['max']})."
                )
            else:
                sentences.append(
                    f"{identity} has {r['my_count']} {r['target_type']}(s), "
                    f"ranking #{r['rank']} out of {r['total_peers']} peers — "
                    f"near the average of {r['avg']}."
                )
        else:
            sentences.append(f"{identity} is present in the approved graph with {sum(len(v) for v in neighbors_by_type.values())} related entities.")

        # Value contribution sentence
        for va in value_aggs:
            share_pct = round(va["value_share"] * 100, 1)
            sentences.append(
                f"Revenue contribution is {_fmt_number(va['my_value'])} "
                f"({share_pct}% of the {_fmt_number(va['total_value'])} total)"
                + (f", ranked #{va['value_rank']}/{va['value_total_peers']} by value" if va.get("value_rank") else "")
                + "."
            )
            # Category concentration
            cats = va.get("category_breakdown", [])
            if cats:
                top_cat = cats[0]
                top_share = round(top_cat["value"] / va["my_value"] * 100) if va["my_value"] > 0 else 0
                if top_share > 30:
                    sentences.append(
                        f"Revenue is concentrated in {top_cat['label']} ({top_share}% of total), "
                        f"followed by {cats[1]['label']}" + (f" and {cats[2]['label']}" if len(cats) > 2 else "") + "."
                    )
                elif len(cats) >= 3:
                    sentences.append(
                        f"Revenue is diversified across {cats[0]['label']}, {cats[1]['label']}, and {cats[2]['label']}."
                    )

        # Trend sentence
        for i, r in enumerate(rankings):
            stats = link_stats[i] if i < len(link_stats) else {}
            yearly = stats.get("yearly", [])
            if len(yearly) >= 2:
                first_yr, last_yr = yearly[0], yearly[-1]
                peak = max(yearly, key=lambda y: y["count"])
                if last_yr["count"] > first_yr["count"] * 1.3:
                    sentences.append(
                        f"Activity grew from {first_yr['count']} ({first_yr['year']}) to {last_yr['count']} ({last_yr['year']})"
                        + (f", peaking at {peak['count']} in {peak['year']}" if peak != last_yr else "")
                        + " — an upward trajectory."
                    )
                elif first_yr["count"] > last_yr["count"] * 1.3:
                    sentences.append(
                        f"Activity declined from {first_yr['count']} ({first_yr['year']}) to {last_yr['count']} ({last_yr['year']})"
                        + (f" after peaking at {peak['count']} in {peak['year']}" if peak != first_yr else "")
                        + " — a downward trend worth investigating."
                    )
                else:
                    sentences.append(
                        f"Activity has been stable across {len(yearly)} years "
                        f"({first_yr['count']}–{last_yr['count']} {r['target_type']}(s) per year)."
                    )

        # Counterparty concentration
        for i, r in enumerate(rankings):
            stats = link_stats[i] if i < len(link_stats) else {}
            distinct = stats.get("distinct_counterparties", {})
            top_cps = stats.get("top_counterparties", {})
            for cp_type, cp_data in top_cps.items():
                items = cp_data.get("items", [])
                total_distinct = distinct.get(cp_type, 0)
                if items and total_distinct > 0 and r["my_count"] > 0:
                    top_share = round(items[0]["count"] / r["my_count"] * 100)
                    if top_share > 20:
                        sentences.append(
                            f"Top {cp_type} {items[0]['label']} accounts for {top_share}% of activity "
                            f"across {total_distinct} distinct {cp_type}(s) — moderate concentration risk."
                        )
                    else:
                        sentences.append(
                            f"Activity is spread across {total_distinct} distinct {cp_type}(s), "
                            f"with no single {cp_type} exceeding {top_share}% — well diversified."
                        )

        return " ".join(sentences)

    # ------------------------------------------------------------------
    # Compose structured output
    # ------------------------------------------------------------------

    def _compose(self, *, center_node, object_type, instance_id, label, cfg,
                 entity_desc, descriptions, props, self_refs, rankings,
                 link_stats, value_aggs, source_key_profile, neighbors_by_type, nodes, edges, question):

        key_facts = []
        interpretations = []
        source_table = cfg.get("table", object_type)

        # -- Base info --
        if props:
            notable = [p for p in props if p["col"] not in ("photo", "notes", "photoPath")][:8]
            prop_text = "; ".join(f"{p['col']}: {p['value']}" for p in notable)
            key_facts.append({"label": f"{label} attributes", "value": prop_text, "source_ref": f"{source_table}"})

        # -- Self-references --
        for fk_col, ref in self_refs.items():
            key_facts.append({"label": f"{fk_col}", "value": f"{ref['label']} ({object_type}:{ref['id']})", "source_ref": source_table})

        # -- Entity type context --
        if entity_desc:
            interpretations.append(f"[{object_type} definition] {entity_desc}")

        # -- Per-link ranking + deep stats --
        for i, r in enumerate(rankings):
            link_desc = descriptions.get(r["link"], "")
            role_text = link_desc.split(".")[0].strip() if link_desc else f"related {r['target_type']}"
            top_pct = 100 - r["percentile"] if r["percentile"] < 100 else 1

            # Ranking fact
            fact_parts = [
                f"{r['my_count']} {r['target_type']}(s)",
                f"ranked #{r['rank']}/{r['total_peers']} (top {top_pct}%)",
                f"avg {r['avg']}, max {r['max']}, {r['level']}",
            ]

            # Enrich with deep stats
            stats = link_stats[i] if i < len(link_stats) else {}

            if stats.get("date_range"):
                dr = stats["date_range"]
                fact_parts.append(f"period {dr['min']} to {dr['max']}")

            if stats.get("numeric_stats"):
                for col_name, ns in stats["numeric_stats"].items():
                    if ns["sum"] > 0:
                        fact_parts.append(f"{col_name} total {_fmt_number(ns['sum'])}, avg {_fmt_number(ns['avg'])}")

            if stats.get("distinct_counterparties"):
                for cp_type, cnt in stats["distinct_counterparties"].items():
                    fact_parts.append(f"{cnt} distinct {cp_type}(s)")

            key_facts.append({
                "label": f"{r['target_type']} ranking",
                "value": "; ".join(fact_parts),
                "source_ref": f"{r['fk_table']} GROUP BY {object_type}",
                "context": role_text,
            })

            # Top counterparties
            if stats.get("top_counterparties"):
                for cp_type, cp_data in stats["top_counterparties"].items():
                    items = cp_data["items"]
                    top_text = ", ".join(f"{it['label']} ({it['count']})" for it in items[:5])
                    top_count = items[0]["count"] if items else 0
                    total = r["my_count"]
                    share = round(top_count / total * 100) if total else 0
                    key_facts.append({
                        "label": f"top {cp_type}(s)",
                        "value": top_text,
                        "source_ref": f"{stats.get('link', '')} counterparties",
                    })
                    if share > 25:
                        interpretations.append(
                            f"Top {cp_type} ({items[0]['label']}) accounts for {share}% of {label}'s {r['target_type']}(s) — concentrated dependency."
                        )

            # Yearly trend
            if stats.get("yearly"):
                yearly = stats["yearly"]
                trend_text = ", ".join(f"{y['year']}: {y['count']}" for y in yearly)
                peak = max(yearly, key=lambda y: y["count"])
                key_facts.append({
                    "label": f"{r['target_type']} yearly trend",
                    "value": trend_text,
                    "source_ref": f"{r['fk_table']} yearly",
                })
                if len(yearly) > 1:
                    first, last = yearly[0]["count"], yearly[-1]["count"]
                    if last > first * 1.5:
                        interpretations.append(f"{label}'s {r['target_type']} volume grew significantly: {first} → {last} (peak {peak['year']}: {peak['count']}).")
                    elif first > last * 1.5:
                        interpretations.append(f"{label}'s {r['target_type']} volume declined: {first} → {last}.")

            # Ranking interpretation
            if r["my_count"] == 0:
                interpretations.append(f"{label} has no directly related {r['target_type']}(s).")
            elif r["level"] == "high":
                interpretations.append(
                    f"{label}'s {r['target_type']} count ({r['my_count']}) ranks #{r['rank']}/{r['total_peers']}, "
                    f"significantly above avg {r['avg']} — high activity entity."
                    + (f" Context: {link_desc.split('.')[0]}." if link_desc else "")
                )
            elif r["level"] == "low":
                interpretations.append(
                    f"{label}'s {r['target_type']} count ({r['my_count']}) ranks #{r['rank']}/{r['total_peers']}, "
                    f"below avg {r['avg']} — review activity level or data completeness."
                )
            else:
                interpretations.append(
                    f"{label}'s {r['target_type']} count ({r['my_count']}) ranks #{r['rank']}/{r['total_peers']}, "
                    f"near avg {r['avg']} — average level."
                )

        if source_key_profile and source_key_profile.get("related_tables"):
            related_tables = source_key_profile.get("related_tables") or []
            top_paths = source_key_profile.get("top_paths") or []
            total_key_rows = source_key_profile.get("total_key_rows", 0)
            distinct_path_labels = sum(int(t.get("distinct_labels") or 0) for t in related_tables)
            key_facts.append({
                "label": "source-key degree",
                "value": (
                    f"{total_key_rows} matching source rows across {len(related_tables)} source table(s); "
                    f"{distinct_path_labels} distinct path label(s)"
                ),
                "source_ref": f"source tables sharing {source_key_profile.get('center_key_col')}",
            })
            table_text = "; ".join(
                f"{t['table']}: {t['row_count']} rows"
                + (f", {t['distinct_labels']} distinct {t['label_col']}" if t.get("label_col") else "")
                for t in related_tables[:6]
            )
            key_facts.append({
                "label": "connected source tables",
                "value": table_text,
                "source_ref": "schema/profile evidence",
            })
            if top_paths:
                path_text = "; ".join(
                    f"{p['label']} via {p['table']} ({p['metric']} {_fmt_number(p['metric_value'])}, {p['row_count']} rows)"
                    for p in top_paths[:5]
                )
                key_facts.append({
                    "label": "top chokepoint/risk paths",
                    "value": path_text,
                    "source_ref": "source-key metric aggregation",
                })
                interpretations.append(
                    f"{label} is not just present in the graph: it has {total_key_rows} source-backed relationship rows. "
                    f"The highest metric paths are {', '.join(p['label'] for p in top_paths[:3])}, so review should start from those connected chokepoints/routes."
                )
            else:
                interpretations.append(
                    f"{label} has {total_key_rows} source-backed relationship rows, but no numeric risk/trade metric column was found for ranking connected paths."
                )
            second_hop_paths = source_key_profile.get("second_hop_paths") or []
            if second_hop_paths:
                second_hop_text = "; ".join(
                    f"{p['label']} -> {', '.join(peer['key'] for peer in p.get('top_peers', [])[:6])}"
                    for p in second_hop_paths[:5]
                )
                key_facts.append({
                    "label": f"depth-{source_key_profile.get('scope_depth', 2)} shared path peers",
                    "value": second_hop_text,
                    "source_ref": "source-key path peer aggregation",
                })
                interpretations.append(
                    f"Depth {source_key_profile.get('scope_depth', 2)} changes the reading from a single-country profile to shared-path exposure: "
                    f"{', '.join(p['label'] for p in second_hop_paths[:3])} connect {label} with other high-exposure countries such as "
                    f"{', '.join(peer['key'] for p in second_hop_paths[:2] for peer in p.get('top_peers', [])[:3])}."
                )

        # -- Multi-hop value aggregation --
        for va in value_aggs:
            share_pct = round(va["value_share"] * 100, 1)
            key_facts.append({
                "label": f"value ({va['value_expr']})",
                "value": (
                    f"{_fmt_number(va['my_value'])} "
                    f"({share_pct}% of total {_fmt_number(va['total_value'])})"
                    + (f", ranked #{va['value_rank']}/{va['value_total_peers']}" if va.get("value_rank") else "")
                ),
                "source_ref": f"{va['detail_table']} aggregation",
            })
            if va.get("category_breakdown"):
                cat_text = "; ".join(f"{c['label']}: {_fmt_number(c['value'])} ({c['count']} items)" for c in va["category_breakdown"][:5])
                key_facts.append({
                    "label": "breakdown by category",
                    "value": cat_text,
                    "source_ref": f"{va['detail_table']} + category join",
                })
            if share_pct > 0:
                rank_text = f", ranked #{va['value_rank']}/{va['value_total_peers']}" if va.get("value_rank") else ""
                interpretations.append(
                    f"{label} contributes {share_pct}% of total value ({_fmt_number(va['my_value'])} / {_fmt_number(va['total_value'])}){rank_text}."
                )

        # -- Unranked neighbor types --
        for ntype, nlist in sorted(neighbors_by_type.items()):
            if any(r["target_type"] == ntype.lower() for r in rankings):
                continue
            samples = ", ".join(n.get("label", n["id"]) for n in nlist[:5])
            suffix = f" and {len(nlist) - 5} more" if len(nlist) > 5 else ""
            key_facts.append({
                "label": f"related {ntype}",
                "value": f"{len(nlist)}: {samples}{suffix}",
                "source_ref": "graph edges",
            })

        if not interpretations:
            interpretations.append(f"{label} has {len(edges)} direct relationships in the approved graph.")

        # --- Profile summary (analytical narrative) ---
        profile_summary = self._build_narrative(
            label, object_type, entity_desc, props, self_refs,
            rankings, link_stats, value_aggs, neighbors_by_type, question,
            source_key_profile=source_key_profile,
        )

        # --- Title ---
        ranking_highlights = []
        for r in rankings:
            ranking_highlights.append(f"{r['my_count']} {r['target_type']}(s) (#{r['rank']}/{r['total_peers']}, {r['level']})")
        value_highlights = []
        for va in value_aggs:
            value_highlights.append(f"value {_fmt_number(va['my_value'])} ({round(va['value_share'] * 100, 1)}% share)")
        source_key_highlights = []
        if source_key_profile and source_key_profile.get("related_tables"):
            source_key_highlights.append(f"{source_key_profile.get('total_key_rows', 0)} source rows")
            top_paths = source_key_profile.get("top_paths") or []
            if top_paths:
                source_key_highlights.append(f"top path {top_paths[0]['label']}")
        title_parts = source_key_highlights[:2] or ranking_highlights[:2]
        if value_highlights and len(title_parts) < 2:
            title_parts.extend(value_highlights[:1])
        source_tables = [
            str(t.get("table") or "")
            for t in (source_key_profile or {}).get("related_tables", [])
        ]
        source_profile_label = (
            "Maritime Exposure Profile"
            if any(table.startswith("maritime_") for table in source_tables)
            else "Source Evidence Profile"
        )
        title = f"{label} {source_profile_label if source_key_profile else 'Business Profile'}"
        if title_parts:
            title += ": " + ", ".join(title_parts)

        return {
            "title": title,
            "profile_summary": profile_summary,
            "key_facts": key_facts,
            "business_interpretation": interpretations,
            "evidence_limits": [
                f"Profile based on {source_table} source table and approved graph controlled aggregation.",
                "Rankings reflect a current snapshot — no time-series trends or external benchmarks.",
                "Conclusions are based solely on the approved graph; external benchmarks, thresholds, and unapproved evidence are not included.",
            ],
            "next_questions": [
                f"How do {label}'s relationship patterns change over time?",
                f"How does {label} compare to typical {object_type}(s) in the same segment?",
                "Are there anomalous patterns or potential risks?",
            ],
            "metrics": {
                "center_node": center_node,
                "object_type": object_type,
                "instance_id": instance_id,
                "label": label,
                "neighbor_count": len(nodes) - 1,
                "edge_count": len(edges),
                "neighbor_types": {k: len(v) for k, v in neighbors_by_type.items()},
                "rankings": rankings,
                "link_stats": link_stats,
                "value_aggregations": value_aggs,
                "source_key_profile": source_key_profile,
            },
        }

    # ------------------------------------------------------------------
    # Streaming analysis (yields step events)
    # ------------------------------------------------------------------

    def analyze_streaming(self, tenant, center_node, question=None):
        """Yields (event_name, data) tuples for SSE streaming."""
        if not center_node or ":" not in center_node:
            yield ("error", {"message": "No center_node provided"})
            return
        object_type, instance_id = center_node.split(":", 1)
        entity_config = self._entity_config(tenant)
        link_config = self._link_config(tenant)
        cfg = entity_config.get(object_type.lower())
        if not cfg:
            yield ("error", {"message": f"Unknown entity type: {object_type}"})
            return

        steps = ["graph_query", "base_entity"]
        relevant_links = [lc for lc in link_config if lc["from"] == object_type.lower() and not lc.get("reverse")]
        for lc in relevant_links:
            steps.append(f"link_analysis:{lc['link']}")
        nm_links = [lc for lc in link_config if lc["from"] == object_type.lower() and lc.get("target_fk")]
        if nm_links:
            steps.append("value_aggregation")
        steps.append("compose_narrative")

        yield ("plan", {"query_plan": [f"Step {i+1}: {s}" for i, s in enumerate(steps)], "steps": steps})

        result = self.analyze(tenant, center_node, question)

        step_idx = 0
        for s in steps:
            step_idx += 1
            yield ("step", {"tool": s, "status": "completed", "step": step_idx, "total": len(steps)})

        if result:
            yield ("evidence", {"evidence_paths": result.get("metrics", {}).get("rankings", [])})
            yield ("structured_answer", {"structured_answer": result})
        else:
            yield ("error", {"message": "Analysis produced no result"})
