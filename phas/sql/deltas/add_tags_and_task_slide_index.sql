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