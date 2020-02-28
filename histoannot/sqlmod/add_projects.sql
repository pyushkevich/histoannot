
/* Project stuff */
DROP TABLE IF EXISTS project;
CREATE TABLE project (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  display_name TEXT UNIQUE NOT NULL,
  desc TEXT,
  url TEXT,
  icon bytea
);

/* Unique constraint on block means it can belong to only one project */
DROP TABLE IF EXISTS project_block;
CREATE TABLE project_block (
  project INT NOT NULL,
  block INT UNIQUE NOT NULL,
  PRIMARY KEY (project,block),
  FOREIGN KEY (project) REFERENCES project(id),
  FOREIGN KEY (block) REFERENCES block(id)
);

/* Unique constraint on task means it can belong to only one project */
DROP TABLE IF EXISTS project_task;
CREATE TABLE project_task (
  project INT NOT NULL,
  task INT UNIQUE NOT NULL,
  PRIMARY KEY (project,task),
  FOREIGN KEY (project) REFERENCES project(id),
  FOREIGN KEY (task) REFERENCES task(id)
);

DROP TABLE IF EXISTS project_access;
CREATE TABLE project_access (
  user INTEGER NOT NULL,
  project INTEGER NOT NULL,
  PRIMARY KEY (user, project),
  FOREIGN KEY (user) REFERENCES user (id),
  FOREIGN KEY (project) REFERENCES project (id)
);


