/* Create a view for quick access to slide extended information */
DROP VIEW IF EXISTS slide_info;
CREATE VIEW slide_info AS
   SELECT S.id, block_id, section, S.slide, stain, slide_name, slide_ext, project, specimen, block_name, private_name AS specimen_private, 
          CASE WHEN public_name IS NULL THEN printf('Anon %04d', specimen) ELSE public_name END AS specimen_public
   FROM slide S LEFT JOIN block B on S.block_id = B.id
                LEFT JOIN specimen SP on B.specimen = SP.id;

/* An anonymized slide info view */
DROP VIEW IF EXISTS slide_info_anon;
CREATE VIEW slide_info_anon AS
   SELECT S.id, block_id, section, S.slide, stain, NULL as slide_name, slide_ext, project, specimen, block_name, NULL AS specimen_private, 
          CASE WHEN public_name IS NULL THEN printf('Anon %04d', specimen) ELSE public_name END AS specimen_public
   FROM slide S LEFT JOIN block B on S.block_id = B.id
                LEFT JOIN specimen SP on B.specimen = SP.id;

/* Create a view for quick access to block extended information */
DROP VIEW IF EXISTS block_info;
CREATE VIEW block_info AS
   SELECT B.*, project, private_name as specimen_private, 
          CASE WHEN public_name IS NULL THEN printf('Anon %04d', specimen) ELSE public_name END AS specimen_public
   FROM block B LEFT JOIN specimen S on B.specimen = S.id;

/* Create a view for quick access to block extended information */
DROP VIEW IF EXISTS block_info_anon;
CREATE VIEW block_info_anon AS
   SELECT B.*, project, NULL as specimen_private, 
          CASE WHEN public_name IS NULL THEN printf('Anon %04d', specimen) ELSE public_name END AS specimen_public
   FROM block B LEFT JOIN specimen S on B.specimen = S.id;

/* Create a view for quick access to tasks */
DROP VIEW IF EXISTS task_info;
CREATE VIEW task_info AS
   SELECT T.*, PT.project as project, TR.referenced_task as referenced_task
   FROM task T 
      LEFT JOIN project_task PT on T.id = PT.task_id
      LEFT JOIN task_ref TR on T.id = TR.task;

DROP VIEW IF EXISTS labelset_info;
CREATE VIEW labelset_info AS
   SELECT L.*, PL.project as project
   FROM labelset L LEFT JOIN project_labelset PL on L.id = PL.labelset_id;

/* A slide info view with task and display name */
DROP VIEW IF EXISTS task_slide_info;
CREATE VIEW task_slide_info AS
    SELECT S.*, TSI.task_id, specimen_private AS specimen_display
    FROM task_slide_index TSI
    LEFT JOIN slide_info S on S.id = TSI.slide
    LEFT JOIN task T on TSI.task_id = T.id;

/* A slide info view with task and display name */
DROP VIEW IF EXISTS task_slide_info_anon;
CREATE VIEW task_slide_info_anon AS
    SELECT S.*, TSI.task_id, specimen_public AS specimen_display
    FROM task_slide_index TSI
    LEFT JOIN slide_info_anon S on S.id = TSI.slide
    LEFT JOIN task T on TSI.task_id = T.id;

/* 
A combined view of task and project access. The column task_access is inferred from both project
an task access entries. If the task is not restricted, the project access is used as task access.
*/
DROP VIEW IF EXISTS task_project_access;
CREATE VIEW task_project_access AS
   SELECT PA.project as project, TI.id as task, PA.user as user, 
          PA.access as project_access, api_permission, anon_permission, 
          TI.restrict_access, 
          CASE WHEN TI.restrict_access = 0 THEN PA.access ELSE IFNULL(TA.access,'none') END as task_access
   FROM project_access PA 
   LEFT JOIN task_info TI on PA.project=TI.project 
   LEFT JOIN task_access TA on PA.user=TA.user and TI.id=TA.task;

/* A view of group membership that includes each user as their own group */
DROP VIEW IF EXISTS effective_group_membership;
CREATE VIEW effective_group_membership AS
   SELECT user_id, group_id FROM group_membership 
   UNION ALL 
   SELECT U.id AS user_id, U.id AS group_id FROM user U WHERE is_group=0 
   ORDER BY user_id, group_id;

/* A view of project access that includes group memberships */
DROP VIEW IF EXISTS effective_project_access;
CREATE VIEW effective_project_access AS
   SELECT user_id AS user, project, 
          CASE max_num_acc WHEN 0 THEN 'none' WHEN 1 THEN 'read' WHEN 2 THEN 'write' WHEN 3 THEN 'admin' END AS access, 
          anon_permission, api_permission 
   FROM (
      SELECT PA.project, GM.user_id,
             MAX(CASE PA.access WHEN 'none' THEN 0 WHEN 'read' THEN 1 WHEN 'write' THEN 2 WHEN 'admin' THEN 3 END) AS max_num_acc, 
             MAX(api_permission) AS api_permission, MAX(anon_permission) AS anon_permission 
      FROM project_access PA
      LEFT JOIN effective_group_membership GM ON GM.group_id = PA.user 
      GROUP BY PA.project, GM.user_id);

/* 
A view of task access that includes group memberships. This is the view that should be used to test if
a user has access to a task because it checkes both direct user access and group-based access, and because
it considers project-level and task-level access settings.
 */
DROP VIEW IF EXISTS effective_task_access;
CREATE VIEW effective_task_access AS
SELECT project, task, user_id as user,
       CASE mna_task WHEN 0 THEN 'none' WHEN 1 THEN 'read' WHEN 2 THEN 'write' WHEN 3 THEN 'admin' END AS access,
       anon_permission, api_permission
FROM (
   SELECT TPA.project, TPA.task, GM.user_id,
          MAX(CASE TPA.task_access WHEN 'none' THEN 0 WHEN 'read' THEN 1 WHEN 'write' THEN 2 WHEN 'admin' THEN 3 END) AS mna_task,
          MAX(api_permission) AS api_permission, MAX(anon_permission) AS anon_permission
   FROM task_project_access TPA
   LEFT JOIN effective_group_membership GM ON GM.group_id = TPA.user
   GROUP BY TPA.task, GM.user_id);
   

/* Defunct view created in prior versions of the code */
DROP VIEW IF EXISTS effective_task_project_access;

/* A view of project access that includes group memberships and maximum task access */
DROP VIEW IF EXISTS effective_project_and_max_task_access;
CREATE VIEW effective_project_and_max_task_access AS
   SELECT project, user, access as project_access, anon_permission, api_permission, 
          CASE max_task_access WHEN 0 THEN 'none' WHEN 1 THEN 'read' WHEN 2 THEN 'write' WHEN 3 THEN 'admin' END AS max_task_access 
   FROM (
      SELECT EPA.*, MAX(CASE ETA.access WHEN 'none' THEN 0 WHEN 'read' THEN 1 WHEN 'write' THEN 2 WHEN 'admin' THEN 3 END) AS max_task_access 
      FROM effective_project_access EPA LEFT JOIN effective_task_access ETA on EPA.project = ETA.project AND EPA.user = ETA.user
      GROUP BY EPA.project, EPA.user)
   ORDER BY project, user;

/* A view of slide access - maximum access that the user has to a slide */
DROP VIEW IF EXISTS effective_slide_access;
CREATE VIEW effective_slide_access AS
   SELECT slide, user, 
          MAX(anon_permission) AS anon_permission, 
          MAX(api_permission) AS api_permission, 
          CASE MAX(acc) WHEN 0 THEN 'none' WHEN 1 THEN 'read' WHEN 2 THEN 'write' WHEN 3 THEN 'admin' END AS slide_access 
   FROM (
      SELECT TSI.slide, TA.user, TA.anon_permission, TA.api_permission,
             CASE TA.access WHEN 'none' THEN 0 WHEN 'read' THEN 1 WHEN 'write' THEN 2 WHEN 'admin' THEN 3 END AS acc
      FROM task_slide_index TSI 
      LEFT JOIN effective_task_access TA ON TSI.task_id = TA.task) 
   GROUP BY slide, user;