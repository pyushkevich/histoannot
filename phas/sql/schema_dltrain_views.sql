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


/* Create a view for listing training samples quickly - with anonymization */
DROP VIEW IF EXISTS training_sample_info_anon;
CREATE VIEW training_sample_info_anon AS
   SELECT T.id,T.task,T.label as label_id,T.have_patch,x0,y0,x1,y1,
      UC.username as creator, t_create,
      UE.username as editor, t_edit,
      datetime(t_create,'unixepoch','localtime') as dt_create,
      datetime(t_edit,'unixepoch','localtime') as dt_edit,
      S.specimen_display as specimen_name,S.block_name,S.section,S.slide,S.stain,S.id as slide_id,
      L.name as label_name
   FROM training_sample T LEFT JOIN edit_meta M on T.meta_id = M.id
      LEFT JOIN task_slide_info_anon S on S.id = T.slide and S.task_id = T.task
      LEFT JOIN user UC on UC.id = M.creator
      LEFT JOIN user UE on UE.id = M.editor
      LEFT JOIN label L on L.id = T.label;


/* Create a view for listing sampling ROIs quickly */
DROP VIEW IF EXISTS sampling_roi_info;
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


/* Create a view for listing sampling ROIs quickly - with anonymization */
DROP VIEW IF EXISTS sampling_roi_info_anon;
CREATE VIEW sampling_roi_info_anon AS
   SELECT T.id,T.task,T.label as label_id,x0,y0,x1,y1,
      UC.username as creator, t_create,
      UE.username as editor, t_edit,
      datetime(t_create,'unixepoch','localtime') as dt_create,
      datetime(t_edit,'unixepoch','localtime') as dt_edit,
      S.specimen_display as specimen_name,S.block_name,S.section,S.slide,S.stain,S.id as slide_id,
      L.name as label_name
   FROM sampling_roi T LEFT JOIN edit_meta M on T.meta_id = M.id
      LEFT JOIN task_slide_info_anon S on S.id = T.slide and S.task_id = T.task
      LEFT JOIN user UC on UC.id = M.creator
      LEFT JOIN user UE on UE.id = M.editor
      LEFT JOIN label L on L.id = T.label;