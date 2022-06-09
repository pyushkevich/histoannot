/* Create a view for quick access to slide extended information */
DROP VIEW IF EXISTS slide_info;
CREATE VIEW slide_info AS
   SELECT S.*, project, specimen, block_name, private_name AS specimen_private, 
          CASE WHEN public_name IS NULL THEN printf("Anon %04d", specimen) ELSE public_name END AS specimen_public
   FROM slide S LEFT JOIN block B on S.block_id = B.id
                LEFT JOIN specimen SP on B.specimen = SP.id;

/* Create a view for quick access to block extended information */
DROP VIEW IF EXISTS block_info;
CREATE VIEW block_info AS
   SELECT B.*, project, private_name as specimen_private, 
          CASE WHEN public_name IS NULL THEN printf("Anon %04d", specimen) ELSE public_name END AS specimen_public
   FROM block B LEFT JOIN specimen S on B.specimen = S.id;

/* Create a view for quick access to tasks */
DROP VIEW IF EXISTS task_info;
CREATE VIEW task_info AS
   SELECT T.*, PT.project as project
   FROM task T LEFT JOIN project_task PT on T.id = PT.task_id;

DROP VIEW IF EXISTS labelset_info;
CREATE VIEW labelset_info AS
   SELECT L.*, PL.project as project
   FROM labelset L LEFT JOIN project_labelset PL on L.id = PL.labelset_id;

/* A slide info view with task and display name */
DROP VIEW IF EXISTS task_slide_info;
CREATE VIEW task_slide_info AS
    SELECT S.*, TSI.task_id, CASE WHEN T.anonymize > 0 THEN specimen_public ELSE specimen_private END AS specimen_display
    FROM task_slide_index TSI
    LEFT JOIN slide_info S on S.id = TSI.slide
    LEFT JOIN task T on TSI.task_id = T.id;

