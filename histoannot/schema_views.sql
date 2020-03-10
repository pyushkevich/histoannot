/* Create a view for quick access to slide extended information */
DROP VIEW IF EXISTS slide_info;
CREATE VIEW slide_info AS
   SELECT S.*, B.block_name, B.specimen_name, PB.project as project
   FROM slide S LEFT JOIN block B on S.block_id = B.id
                LEFT JOIN project_block PB on B.id = PB.block;

/* Create a view for quick access to block extended information */
DROP VIEW IF EXISTS block_info;
CREATE VIEW block_info AS
   SELECT B.*, PB.project as project
   FROM block B LEFT JOIN project_block PB on B.id = PB.block;

/* Create a view for quick access to tasks */
DROP VIEW IF EXISTS task_info;
CREATE VIEW task_info AS
   SELECT T.*, PT.project as project
   FROM task T LEFT JOIN project_task PT on T.id = PT.task_id;

DROP VIEW IF EXISTS labelset_info;
CREATE VIEW labelset_info AS
   SELECT L.*, PL.project as project
   FROM labelset L LEFT JOIN project_labelset PL on L.id = PL.labelset_id;