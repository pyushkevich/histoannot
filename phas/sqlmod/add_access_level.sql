/* Update project access table */
ALTER TABLE project_access ADD COLUMN access TEXT CHECK(access in ('none','read','write','admin')) NOT NULL DEFAULT 'none';
UPDATE project_access SET access='read' WHERE admin is FALSE;
UPDATE project_access SET access='admin' WHERE admin is TRUE;
ALTER TABLE project_access DROP COLUMN admin;

/* Update task access table */
ALTER TABLE task_access ADD COLUMN access TEXT CHECK(access in ('none','read','write','admin')) NOT NULL DEFAULT 'none';
UPDATE task_access SET access='read';

