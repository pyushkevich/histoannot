DROP TABLE IF EXISTS project;
CREATE TABLE PROJECT (
    id TEXT PRIMARY KEY,
    disp_name TEXT NOT NULL,
    desc TEXT,
    base_url TEXT NOT NULL,
    json TEXT NOT NULL
);

DROP TABLE IF EXISTS user;
CREATE TABLE user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password TEXT,
  email TEXT,
  disabled BOOLEAN NOT NULL DEFAULT (0),
  site_admin BOOLEAN NOT NULL DEFAULT(0)
);

DROP TABLE IF EXISTS password_reset;
CREATE TABLE password_reset (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reset_key TEXT NOT NULL,
  user INT NOT NULL,
  t_expires INTEGER NOT NULL,
  activated BOOLEAN NOT NULL DEFAULT(0),
  FOREIGN KEY(user) REFERENCES user(id)
);

DROP TABLE IF EXISTS user_api_key;
CREATE TABLE user_api_key (
  api_key TEXT PRIMARY KEY,
  user INT NOT NULL,
  t_expires INTEGER NOT NULL,
  FOREIGN KEY(user) REFERENCES user(id)
);

/*
DROP TABLE IF EXISTS block;
CREATE TABLE block (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  specimen_name TEXT NOT NULL,
  block_name TEXT NOT NULL
);
*/

/* A table for specimens. This was a big mistake not to create in the first place */
DROP TABLE IF EXISTS specimen;
CREATE TABLE specimen (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project TEXT NOT NULL,
  private_name TEXT NOT NULL,
  public_name TEXT,
  FOREIGN KEY(project) REFERENCES project(id),
  UNIQUE(project, private_name)
);

DROP TABLE IF EXISTS block;
CREATE TABLE block (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  specimen INTEGER NOT NULL,
  block_name TEXT NOT NULL,
  UNIQUE(specimen, block_name)
);

DROP TABLE IF EXISTS slide;
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
    REFERENCES block(id)
    ON DELETE CASCADE
);


DROP TABLE IF EXISTS task;
CREATE TABLE task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  json TEXT NOT NULL,
  restrict_access BOOLEAN NOT NULL,
  anonymize BOOLEAN DEFAULT(0) NOT NULL
);


DROP TABLE IF EXISTS task_access;
CREATE TABLE task_access (
  user INTEGER NOT NULL,
  task INTEGER NOT NULL,
  access TEXT CHECK(access in ('none','read','write','admin')) NOT NULL DEFAULT 'none',
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



/* Unique constraint on block means it can belong to only one project */
/*
DROP TABLE IF EXISTS project_block;
CREATE TABLE project_block (
  project TEXT NOT NULL,
  block INT UNIQUE NOT NULL,
  PRIMARY KEY(project,block),
  FOREIGN KEY(project) REFERENCES project(id),
  FOREIGN KEY(block) REFERENCES block(id)
);
*/

/* A project-specimen mapping that contstraints specimen names in a project to be unique */
DROP TABLE IF EXISTS project_specimen;
CREATE TABLE project_specimen (
  project TEXT NOT NULL,
  specimen_id INT UNIQUE NOT NULL,
  specimen_name TEXT NOT NULL,
  PRIMARY KEY(project, specimen_id),
  FOREIGN KEY(project) REFERENCES project(id),
  FOREIGN KEY(specimen_id) REFERENCES specimen(id),
  FOREIGN KEY(specimen_name) REFERENCES specimen(private_name),
  UNIQUE(project, specimen_name)
);


/* Unique constraint on task means it can belong to only one project */
DROP TABLE IF EXISTS project_task;
CREATE TABLE project_task (
  project TEXT NOT NULL,
  task_id INT UNIQUE NOT NULL,
  task_name TEXT NOT NULL,
  PRIMARY KEY(project,task_name),
  FOREIGN KEY(project) REFERENCES project(id),
  FOREIGN KEY(task_id) REFERENCES task(id),
  FOREIGN KEY(task_name) REFERENCES task(name)
);

DROP TABLE IF EXISTS project_access;
CREATE TABLE project_access (
  user INTEGER NOT NULL,
  project TEXT NOT NULL,
  access TEXT CHECK(access in ('none','read','write','admin')) NOT NULL DEFAULT 'none',
  anon_permission BOOLEAN DEFAULT(0) NOT NULL,
  api_permission BOOLEAN DEFAULT(0) NOT NULL,
  PRIMARY KEY(user, project),
  FOREIGN KEY(project) REFERENCES project(id),
  FOREIGN KEY (user) REFERENCES user (id)
);

DROP TABLE IF EXISTS user_task_slide_preferences;
CREATE TABLE user_task_slide_preferences (
    user INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    slide INTEGER NOT NULL,
    json TEXT,
    PRIMARY KEY (user, task_id, slide),
    FOREIGN KEY (user) REFERENCES user (id),
    FOREIGN KEY (task_id) REFERENCES task(id),
    FOREIGN KEY (slide) REFERENCES slide(id)
);

/* A way to assign text tags to slides. Tags may be external (from external manifest) or
   internal (assigned within the system) */
DROP TABLE IF EXISTS slide_tags;
CREATE TABLE slide_tags (
    slide INTEGER NOT NULL,
    tag TEXT NOT NULL,
    external BOOLEAN NOT NULL DEFAULT(1),
    PRIMARY KEY (slide, tag),
    FOREIGN KEY (slide) REFERENCES slide(id)
);

/* A cached N:M mapping of slides to tasks that must be updated whenever a task changes
   or new slides are imported */
DROP TABLE IF EXISTS task_slide_index;
CREATE TABLE task_slide_index (
    slide INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    PRIMARY KEY (slide, task_id),
    FOREIGN KEY (slide) REFERENCES slide(id),
    FOREIGN KEY (task_id) REFERENCES task(id)
);

