from django.db import migrations

REPAIR_TASK_TABLE_SQL = """
DO $$
DECLARE
    pk_name text;
BEGIN
    -- 1. Ensure 'id' exists and is primary key
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='coresite_task' AND column_name='id'
    ) THEN
        SELECT constraint_name INTO pk_name
        FROM information_schema.table_constraints
        WHERE table_name = 'coresite_task' AND constraint_type = 'PRIMARY KEY';

        IF pk_name IS NOT NULL THEN
            EXECUTE 'ALTER TABLE coresite_task DROP CONSTRAINT ' || quote_ident(pk_name);
        END IF;

        ALTER TABLE coresite_task ADD COLUMN id bigserial PRIMARY KEY;
    END IF;

    -- 2. Ensure 'description' exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='coresite_task' AND column_name='description'
    ) THEN
        ALTER TABLE coresite_task ADD COLUMN description text;
    END IF;

    -- 3. Ensure 'owner_id' exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='coresite_task' AND column_name='owner_id'
    ) THEN
        ALTER TABLE coresite_task ADD COLUMN owner_id integer REFERENCES auth_user(id) ON DELETE CASCADE;
    END IF;

    -- 4. Ensure 'project_id' exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='coresite_task' AND column_name='project_id'
    ) THEN
        ALTER TABLE coresite_task ADD COLUMN project_id bigint REFERENCES coresite_project(id) ON DELETE CASCADE;
    END IF;

    -- 5. Ensure 'ticket_type' exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='coresite_task' AND column_name='ticket_type'
    ) THEN
        ALTER TABLE coresite_task ADD COLUMN ticket_type varchar(10) DEFAULT 'feature' NOT NULL;
    END IF;

    -- 6. Ensure 'subtasks' exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='coresite_task' AND column_name='subtasks'
    ) THEN
        ALTER TABLE coresite_task ADD COLUMN subtasks jsonb DEFAULT '[]'::jsonb NOT NULL;
    END IF;

    -- 7. Ensure 'due_date' exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='coresite_task' AND column_name='due_date'
    ) THEN
        ALTER TABLE coresite_task ADD COLUMN due_date timestamp with time zone;
    END IF;
END $$;
"""


def repair_postgres_schema(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(REPAIR_TASK_TABLE_SQL)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("coresite", "0023_codechunk"),
    ]

    operations = [
        migrations.RunPython(repair_postgres_schema, noop_reverse),
    ]
