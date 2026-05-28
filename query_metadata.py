import os
import argparse
import sys
from sqlalchemy import create_engine, text

DB_URL = os.environ.get('ALETHEIA_PG_URL', f'postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get("ALETHEIA_PG_DB", "aletheia_ontology")}')

def get_engine():
    return create_engine(DB_URL)

def print_schema(conn):
    print("\n=== Ontology Database Schema (Physical Tables) ===")
    res = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).fetchall()
    for row in res:
        print(f" - {row[0]}")

def print_extracted_tables(conn):
    print("\n=== Extracted Tables & Business Context ===")
    res = conn.execute(text("SELECT id, table_name, table_comment FROM aletheia_extracted_tables ORDER BY table_name")).fetchall()
    for row in res:
        comment = row[2] if row[2] else "No business context"
        print(f"[{row[0]}] {row[1]}\n    Context: {comment}")

def print_extracted_columns(conn, table_name=None):
    print(f"\n=== Extracted Columns {('(' + table_name + ')') if table_name else '(All, Limit 50)'} ===")
    query = """
        SELECT t.table_name, c.column_name, c.data_type 
        FROM aletheia_extracted_columns c
        JOIN aletheia_extracted_tables t ON c.table_id = t.id
    """
    if table_name:
        query += f" WHERE t.table_name = '{table_name}'"
    query += " ORDER BY t.table_name, c.id"
    if not table_name:
        query += " LIMIT 50"
    
    res = conn.execute(text(query)).fetchall()
    current_table = ""
    for row in res:
        if row[0] != current_table:
            current_table = row[0]
            print(f"\nTable: {current_table}")
        print(f"  - {row[1]} [{row[2]}]")

def print_business_objects(conn):
    print("\n=== Business Objects ===")
    try:
        res = conn.execute(text("SELECT id, name, description FROM aletheia_schema_object_candidates")).fetchall()
        if not res:
            print("  (No business objects found. Run Object Modeler Agent.)")
        for row in res:
            print(f"[{row[0]}] {row[1]}: {row[2]}")
    except Exception as e:
        print("  (Table not found or error. Run Object Modeler Agent first.)")

def print_business_links(conn):
    print("\n=== Business Links ===")
    try:
        res = conn.execute(text("""
            SELECT l.id, s.name, t.name, l.link_type, l.description 
            FROM aletheia_schema_link_candidates l
            JOIN aletheia_schema_object_candidates s ON l.source_object_id = s.id
            JOIN aletheia_schema_object_candidates t ON l.target_object_id = t.id
        """)).fetchall()
        if not res:
            print("  (No business links found. Run Link Weaver Agent.)")
        for row in res:
            print(f"[{row[0]}] {row[1]} --({row[3]})--> {row[2]}\n    Desc: {row[4]}")
    except Exception as e:
         print("  (Table not found or error. Run Link Weaver Agent first.)")

def print_business_actions(conn):
    print("\n=== Business Actions ===")
    try:
        res = conn.execute(text("SELECT id, name, action_type, is_safe, description FROM aletheia_business_actions")).fetchall()
        if not res:
            print("  (No business actions found. Run Action Synthesizer Agent.)")
        for row in res:
            safe_badge = "✅ SAFE" if row[3] else "⚠️ UNSAFE"
            print(f"[{row[0]}] {row[1]} ({row[2].upper()}) [{safe_badge}]\n    Desc: {row[4]}")
    except Exception as e:
         print("  (Table not found or error. Run Action Synthesizer Agent first.)")

def print_ontology_artifacts(conn):
    print("\n=== Ontology Artifacts ===")
    try:
        res = conn.execute(text("""
            SELECT canonical_key, artifact_type, name, status, version, confidence, source_agent
            FROM aletheia_ontology_artifacts
            ORDER BY artifact_type, canonical_key
        """)).fetchall()
        if not res:
            print("  (No ontology artifacts found. Run Object/Link/Action agents.)")
        for row in res:
            print(
                f"[{row[1]}] {row[0]} status={row[3]} version={row[4]} "
                f"confidence={row[5]} source={row[6]}\n    Name: {row[2]}"
            )
    except Exception:
         print("  (Table not found or error. Run artifact-enabled agents first.)")

def main():
    parser = argparse.ArgumentParser(description="Aletheia Ontology Query Tool", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--schema", action="store_true", help="List all physical tables in the ontology database")
    parser.add_argument("--tables", action="store_true", help="List extracted tables and their business context")
    parser.add_argument("--columns", metavar="TABLE", nargs='?', const='ALL', help="List columns (Usage: --columns orders)")
    parser.add_argument("--objects", action="store_true", help="List AI-generated business objects")
    parser.add_argument("--links", action="store_true", help="List relationships between business objects")
    parser.add_argument("--actions", action="store_true", help="List synthesized business actions (Stored Procedures/Triggers)")
    parser.add_argument("--artifacts", action="store_true", help="List unified ontology artifacts")
    parser.add_argument("--all", action="store_true", help="Print a comprehensive summary of everything")
    
    args = parser.parse_args()

    # If no arguments provided, default to --all
    if not any(vars(args).values()):
        print("No arguments provided. Running comprehensive summary (--all)...\n")
        args.all = True

    engine = get_engine()
    try:
        with engine.connect() as conn:
            if args.all or args.schema:
                print_schema(conn)
            if args.all or args.tables:
                print_extracted_tables(conn)
            if args.all or args.columns:
                t_name = None if args.columns == 'ALL' or args.all else args.columns
                print_extracted_columns(conn, t_name)
            if args.all or args.objects:
                print_business_objects(conn)
            if args.all or args.links:
                print_business_links(conn)
            if args.all or args.actions:
                print_business_actions(conn)
            if args.all or args.artifacts:
                print_ontology_artifacts(conn)
    except Exception as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    main()
