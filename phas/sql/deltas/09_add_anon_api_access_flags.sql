/* Update project access table */
ALTER TABLE project_access ADD COLUMN anon_permission BOOLEAN DEFAULT(0) NOT NULL;
ALTER TABLE project_access ADD COLUMN api_permission BOOLEAN DEFAULT(0) NOT NULL;

/* Set default values for access */
UPDATE project_access SET anon_permission=1 WHERE access = 'admin';
UPDATE project_access SET api_permission=1 WHERE access = 'read' or access = 'write' or access = 'admin';