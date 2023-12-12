DROP TABLE IF EXISTS label;
DROP TABLE IF EXISTS labelset;

CREATE TABLE labelset (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT
);

CREATE TABLE label (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  labelset INTEGER NOT NULL,
  parent_id INTEGER,
  name TEXT NOT NULL,
  description TEXT,
  color TEXT NOT NULL,
  FOREIGN KEY (labelset) REFERENCES labelset(id),
  FOREIGN KEY (parent_id) REFERENCES label(id),
  UNIQUE (labelset, name)
);

DROP TABLE IF EXISTS training_sample;
CREATE TABLE training_sample (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  x0 REAL NOT NULL,
  y0 REAL NOT NULL,
  x1 REAL NOT NULL,
  y1 REAL NOT NULL,
  label INTEGER NOT NULL,
  slide INTEGER NOT NULL,
  task INTEGER NOT NULL,
  meta_id INTEGER NOT NULL,
  have_patch BOOLEAN DEFAULT FALSE NOT NULL,
  FOREIGN KEY (label) REFERENCES label(id),
  FOREIGN KEY (slide) REFERENCES slide(id),
  FOREIGN KEY (task) REFERENCES task(id),
  FOREIGN KEY (meta_id) REFERENCES edit_meta(id)
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

DROP TABLE IF EXISTS sampling_roi;
CREATE TABLE sampling_roi (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  x0 REAL NOT NULL,
  y0 REAL NOT NULL,
  x1 REAL NOT NULL,
  y1 REAL NOT NULL,
  label INTEGER NOT NULL,
  slide INTEGER NOT NULL,
  task INTEGER NOT NULL,
  meta_id INTEGER NOT NULL,
  json BLOB,
  FOREIGN KEY (label) REFERENCES label(id),
  FOREIGN KEY (slide) REFERENCES slide(id),
  FOREIGN KEY (task) REFERENCES task(id),
  FOREIGN KEY (meta_id) REFERENCES edit_meta(id)
);


/* Create a view for listing training samples quickly */
DROP VIEW IF EXISTS training_sample_info;
CREATE VIEW training_sample_info AS
   SELECT T.id,T.task,T.label as label_id,T.have_patch,x0,y0,x1,y1,
      UC.username as creator, t_create,
      UE.username as editor, t_edit,
      datetime(t_create,'unixepoch','localtime') as dt_create,
      datetime(t_edit,'unixepoch','localtime') as dt_edit,
      S.specimen_display as specimen_name,S.block_name,S.section,S.slide,S.stain,S.id as slide_id,
      L.name as label_name
   FROM training_sample T LEFT JOIN edit_meta M on T.meta_id = M.id
      LEFT JOIN task_slide_info S on S.id = T.slide and S.task_id = T.task
      LEFT JOIN user UC on UC.id = M.creator
      LEFT JOIN user UE on UE.id = M.editor
      LEFT JOIN label L on L.id = T.label;


/* Create a view for listing sampling ROIs quickly */
DROP VIEW IF EXISTS training_sampling_roi_info;
CREATE VIEW sampling_roi_info AS
   SELECT T.id,T.task,T.label as label_id,x0,y0,x1,y1,
      UC.username as creator, t_create,
      UE.username as editor, t_edit,
      datetime(t_create,'unixepoch','localtime') as dt_create,
      datetime(t_edit,'unixepoch','localtime') as dt_edit,
      S.specimen_display as specimen_name,S.block_name,S.section,S.slide,S.stain,S.id as slide_id,
      L.name as label_name
   FROM sampling_roi T LEFT JOIN edit_meta M on T.meta_id = M.id
      LEFT JOIN task_slide_info S on S.id = T.slide and S.task_id = T.task
      LEFT JOIN user UC on UC.id = M.creator
      LEFT JOIN user UE on UE.id = M.editor
      LEFT JOIN label L on L.id = T.label;

