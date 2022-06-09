
/* Drop constraint on labelset */
PRAGMA foreign_keys=off;
BEGIN TRANSACTION;

/* Create the specimen table */
DROP TABLE IF EXISTS specimen;
CREATE TABLE specimen (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project TEXT NOT NULL,
  private_name TEXT NOT NULL,
  public_name TEXT,
  FOREIGN KEY(project) REFERENCES project(id),
  UNIQUE(project, private_name)
);

/* Populate the specimen table using current block information */
INSERT INTO specimen(project, private_name)
SELECT DISTINCT project,specimen_name AS private_name 
FROM project_block PB LEFT JOIN BLOCK B ON B.id = PB.block 
ORDER BY project,specimen_name;

/* Create the temporary block table */
DROP TABLE IF EXISTS block_temp;
CREATE TABLE block_temp (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  specimen INTEGER NOT NULL,
  block_name TEXT NOT NULL,
  UNIQUE (specimen, block_name)
);

/* Populate the block table */
insert into block_temp(id, specimen, block_name) 
select B.id, S.id, B.block_name from block B 
    left join project_block PB on B.id = PB.block 
    left join specimen S on S.project=PB.project and S.private_name = B.specimen_name;

/* Remove the view that relies on bold block table */
DROP VIEW IF EXISTS slide_info;
DROP VIEW IF EXISTS block_info;

/* Replace old block table with the new one */
DROP TABLE block;
ALTER TABLE block_temp RENAME TO block;

/* Create a view for quick access to block extended information */
CREATE VIEW block_info AS
   SELECT B.*, project, private_name as specimen_private, 
          CASE WHEN public_name IS NULL THEN printf("Anon %04d", specimen) ELSE public_name END AS specimen_public
   FROM block B LEFT JOIN specimen S on B.specimen = S.id;

/* Create the new slide_info view */
CREATE VIEW slide_info AS
   SELECT S.*, project, specimen, block_name, private_name AS specimen_private, 
          CASE WHEN public_name IS NULL THEN printf("Anon %04d", specimen) ELSE public_name END AS specimen_public
   FROM slide S LEFT JOIN block B on S.block_id = B.id
                LEFT JOIN specimen SP on B.specimen = SP.id;

DROP VIEW IF EXISTS task_slide_info;
CREATE VIEW task_slide_info AS
    SELECT S.*, TSI.task_id, CASE WHEN T.anonymize > 0 THEN specimen_public ELSE specimen_private END AS specimen_display
    FROM task_slide_index TSI
    LEFT JOIN slide_info S on S.id = TSI.slide
    LEFT JOIN task T on TSI.task_id = T.id;

/* Get rid of the project-block mapping */
DROP TABLE IF EXISTS project_block;

/* If made this far, commit */
COMMIT;
PRAGMA foreign_keys=on;

/* Add the anon field to task */
ALTER TABLE task ADD COLUMN anonymize BOOLEAN NOT NULL DEFAULT(0);
