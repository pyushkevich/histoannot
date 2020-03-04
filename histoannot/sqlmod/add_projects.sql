/* Unique constraint on block means it can belong to only one project */
DROP TABLE IF EXISTS project_block;
CREATE TABLE project_block (
  project TEXT NOT NULL,
  block INT UNIQUE NOT NULL,
  PRIMARY KEY(project,block),
  FOREIGN KEY(block) REFERENCES block(id)
);

/* Unique constraint on task means it can belong to only one project */
DROP TABLE IF EXISTS project_task;
CREATE TABLE project_task (
  project TEXT NOT NULL,
  task INT UNIQUE NOT NULL,
  PRIMARY KEY(project,task),
  FOREIGN KEY(task) REFERENCES task(id)
);

DROP TABLE IF EXISTS project_access;
CREATE TABLE project_access (
  user INTEGER NOT NULL,
  project TEXT NOT NULL,
  PRIMARY KEY(user, project),
  FOREIGN KEY (user) REFERENCES user (id)
);

/* Create a view for quick access to slide extended information */
DROP VIEW IF EXISTS slide_info;
CREATE VIEW slide_info AS
   SELECT S.*, B.block_name, B.specimen_name, PB.project as project
   FROM slide S LEFT JOIN block B on S.block_id = B.id
                LEFT JOIN project_block PB on B.id = PB.block;


