DROP TABLE IF EXISTS user;
DROP TABLE IF EXISTS block;
DROP TABLE IF EXISTS slide;

CREATE TABLE user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL
);

CREATE TABLE block (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  specimen_name TEXT NOT NULL,
  block_name TEXT NOT NULL
);

CREATE TABLE slide (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  block_id INTEGER NOT NULL,
  section INTEGER NOT NULL,
  slide INTEGER NOT NULL,
  stain TEXT NOT NULL,
  slide_name TEXT UNIQUE,
  slide_ext TEXT NOT NULL,
  CONSTRAINT fk_block
    FOREIGN KEY (block_id)
    REFERENCES block_id(id)
    ON DELETE CASCADE
);

DROP TABLE IF EXISTS task;
CREATE TABLE task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  json TEXT NOT NULL,
  restrict_access BOOLEAN NOT NULL
);

DROP TABLE IF EXISTS task_access;
CREATE TABLE task_access (
  user INTEGER NOT NULL,
  task INTEGER NOT NULL,
  PRIMARY KEY(user, task),
  FOREIGN KEY (user) REFERENCES user (id),
  FOREIGN KEY (task) REFERENCES task (id)
);

DROP TABLE IF EXISTS edit_meta;
CREATE TABLE edit_meta (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  creator INTEGER NOT NULL,
  editor INTEGER NOT NULL,
  t_create INTEGER NOT NULL,
  t_edit INTEGER NOT NULL,
  FOREIGN KEY(creator) REFERENCES user(id),
  FOREIGN KEY(editor) REFERENCES user(id)
);


DROP TABLE IF EXISTS annot;
CREATE TABLE annot (
  slide_id INTEGER NOT NULL,
  task_id INTEGER NOT NULL,
  json TEXT,
  meta_id INTEGER NOT NULL,
  n_paths INTEGER NOT NULL,
  n_markers INTEGER NOT NULL,
  FOREIGN KEY (slide_id) REFERENCES slide (id),
  FOREIGN KEY (task_id) REFERENCES task (id),
  FOREIGN KEY (meta_id) REFERENCES edit_meta (id),
  PRIMARY KEY (slide_id,task_id)
);

DROP TABLE IF EXISTS dzi_node;
CREATE TABLE dzi_node (
  url TEXT PRIMARY KEY,
  t_ping INTEGER,
  cpu_percent INTEGER
);

DROP TABLE IF EXISTS slide_dzi_node;
CREATE TABLE slide_dzi_node (
  slide_id INTEGER PRIMARY KEY,
  url TEXT,
  FOREIGN KEY (slide_id) REFERENCES slide (id)
);