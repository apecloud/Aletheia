import tempfile
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agents.ontology_artifacts import (
    METADATA_SCHEMA_BASE_VERSION,
    METADATA_SCHEMA_TENANT_UNIQUENESS_VERSION,
    SchemaObjectCandidate,
    ensure_artifact_schema,
    upsert_artifact,
)


class OntologyArtifactSchemaMigrationTest(unittest.TestCase):
    def _engine(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        return create_engine(f"sqlite:///{tmpdir.name}/metadata.db")

    def test_schema_migrations_are_recorded_on_initialization(self):
        engine = self._engine()

        ensure_artifact_schema(engine)

        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT version FROM aletheia_schema_migrations ORDER BY version")
            ).scalars().all()

        self.assertIn(METADATA_SCHEMA_BASE_VERSION, rows)
        self.assertIn(METADATA_SCHEMA_TENANT_UNIQUENESS_VERSION, rows)

    def test_schema_object_candidates_are_unique_per_tenant(self):
        engine = self._engine()
        ensure_artifact_schema(engine)
        Session = sessionmaker(bind=engine)

        with Session() as session:
            session.add(SchemaObjectCandidate(project_id="tenant-a", name="Customer", description="A"))
            session.add(SchemaObjectCandidate(project_id="tenant-b", name="Customer", description="B"))
            session.commit()

        with Session() as session:
            session.add(SchemaObjectCandidate(project_id="tenant-a", name="Customer", description="duplicate"))
            with self.assertRaises(IntegrityError):
                session.commit()

    def test_artifact_upsert_is_tenant_scoped(self):
        engine = self._engine()
        ensure_artifact_schema(engine)
        Session = sessionmaker(bind=engine)

        with Session() as session:
            first = upsert_artifact(
                session,
                artifact_type="object",
                natural_key="Customer",
                name="Customer",
                description="Tenant A customer",
                payload={},
                source_refs=[],
                source_agent="test",
                project_id="tenant-a",
            )
            second = upsert_artifact(
                session,
                artifact_type="object",
                natural_key="Customer",
                name="Customer",
                description="Tenant A customer updated",
                payload={},
                source_refs=[],
                source_agent="test",
                project_id="tenant-a",
            )
            other_tenant = upsert_artifact(
                session,
                artifact_type="object",
                natural_key="Customer",
                name="Customer",
                description="Tenant B customer",
                payload={},
                source_refs=[],
                source_agent="test",
                project_id="tenant-b",
            )
            session.commit()

            self.assertEqual(first.id, second.id)
            self.assertNotEqual(first.id, other_tenant.id)

    def test_legacy_sqlite_schema_object_unique_name_migrates_to_tenant_scope(self):
        engine = self._engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE aletheia_schema_object_candidates (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        project_id VARCHAR(255) NOT NULL DEFAULT 'default',
                        name VARCHAR(255) NOT NULL UNIQUE,
                        description TEXT,
                        created_at DATETIME
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_schema_object_candidates
                        (project_id, name, description)
                    VALUES
                        ('tenant-a', 'Customer', 'legacy customer')
                    """
                )
            )

        ensure_artifact_schema(engine)

        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_schema_object_candidates
                        (project_id, name, description)
                    VALUES
                        ('tenant-b', 'Customer', 'cross-tenant customer')
                    """
                )
            )
            rows = conn.execute(
                text(
                    """
                    SELECT project_id, name
                    FROM aletheia_schema_object_candidates
                    WHERE name = 'Customer'
                    ORDER BY project_id
                    """
                )
            ).fetchall()
            self.assertEqual([(row[0], row[1]) for row in rows], [("tenant-a", "Customer"), ("tenant-b", "Customer")])

            with self.assertRaises(Exception):
                conn.execute(
                    text(
                        """
                        INSERT INTO aletheia_schema_object_candidates
                            (project_id, name, description)
                        VALUES
                            ('tenant-a', 'Customer', 'duplicate same tenant')
                        """
                    )
                )


if __name__ == "__main__":
    unittest.main()
