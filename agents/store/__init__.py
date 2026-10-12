"""The agents' access to 3lay's database (Supabase Postgres, eu-west-1).

The backend owns the schema (Alembic migrations in backend/migrations); this
package only reads and writes the tables the pipeline needs: client
configurations and destination secrets, and in E5 the event records. In
production it connects with its own database role, limited to those tables.
"""
