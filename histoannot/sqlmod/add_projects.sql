/* Add new fields to user */
ALTER TABLE user ADD COLUMN email TEXT;
ALTER TABLE user ADD COLUMN disabled BOOLEAN NOT NULL DEFAULT (0);
ALTER TABLE user ADD COLUMN site_admin BOOLEAN NOT NULL DEFAULT(0);

CREATE TABLE password_reset (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reset_key TEXT NOT NULL,
  user INT NOT NULL,
  t_expires INTEGER NOT NULL,
  activated BOOLEAN NOT NULL DEFAULT(0),
  FOREIGN KEY(user) REFERENCES user(id)
);


/* Drop constraint on labelset */
PRAGMA foreign_keys=off;
BEGIN TRANSACTION;

CREATE TABLE labelset_temp (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT
);

INSERT INTO labelset_temp(id,name,description)
SELECT id,name,description
FROM labelset;

DROP TABLE labelset;
ALTER TABLE labelset_temp RENAME TO labelset;
COMMIT;
PRAGMA foreign_keys=on;

CREATE TABLE PROJECT (
    id TEXT PRIMARY KEY,
    disp_name TEXT NOT NULL,
    desc TEXT,
    base_url TEXT NOT NULL,
    json TEXT NOT NULL
);

/* Unique constraint on block means it can belong to only one project */
CREATE TABLE project_block (
  project TEXT NOT NULL,
  block INT UNIQUE NOT NULL,
  PRIMARY KEY(project,block),
  FOREIGN KEY(project) REFERENCES project(id),
  FOREIGN KEY(block) REFERENCES block(id)
);

/* Unique constraint on task means it can belong to only one project */
CREATE TABLE project_task (
  project TEXT NOT NULL,
  task INT UNIQUE NOT NULL,
  PRIMARY KEY(project,task),
  FOREIGN KEY(project) REFERENCES project(id),
  FOREIGN KEY(task) REFERENCES task(id)
);

CREATE TABLE project_access (
  user INTEGER NOT NULL,
  project TEXT NOT NULL,
  admin BOOLEAN NOT NULL DEFAULT(0),
  PRIMARY KEY(user, project),
  FOREIGN KEY(project) REFERENCES project(id),
  FOREIGN KEY (user) REFERENCES user (id)
);

DROP TABLE IF EXISTS project_labelset;
CREATE TABLE project_labelset (
  project TEXT NOT NULL,
  labelset_name TEXT NOT NULL,
  labelset_id INT NOT NULL,
  PRIMARY KEY(project,labelset_name),
  FOREIGN KEY(project) REFERENCES project(id),
  FOREIGN KEY(labelset_name) REFERENCES labelset(name),
  FOREIGN KEY(labelset_id) REFERENCES labelset(id)
);
