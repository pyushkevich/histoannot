# Permission System Analysis and Implementation

## Problem Statement

The original permission system enforced a constraint that if a user has level X access to a task, they must have X or greater access to the project. This bidirectional synchronization prevented scenarios where:
- A user could have `project='none'` but `task='read'`
- Project-level access could act as a wildcard for non-restricted tasks

## Analysis of Security Implications

### Current System (Before Changes)

**Constraint Enforcement Locations:**
1. `project_ref.py::user_set_access_level()` (lines 344-350)
   - When project access is lowered, all task access levels are automatically lowered
   
2. `project_ref.py::user_set_task_access_level()` (lines 383-393)
   - When task access is raised, project access is automatically elevated

**Access Checking:**
1. `auth.py::_task_access_required()` (line 252)
   - First checks project_access >= min_level
   - Then checks task_access >= min_level (only if restrict_access=1)

**Problem:** This created a hard constraint where task_access could never exceed project_access at rest.

### New System (After Changes)

**Removed Constraints:**
1. No automatic lowering of task permissions when project permission is reduced
2. No automatic elevation of project permission when task permission is increased

**Updated Access Checking:**
```python
if restrict_access > 0:
    # Use task-level access for restricted tasks
    check(task_access >= min_level)
else:
    # Use project-level access as wildcard for non-restricted tasks
    check(project_access >= min_level)
```

**Key Principle:** Project-level permissions act as a **wildcard** for non-restricted tasks, while task-level permissions **override** for restricted tasks.

## Security Analysis

### Potential Security Concerns Addressed

#### 1. Unauthorized Access via Task Permissions
**Concern:** Could a user gain unauthorized access by having task permission but no project permission?

**Analysis:** No security issue because:
- Access is still checked at the appropriate level (task or project)
- The `restrict_access` flag controls which permission is checked
- Both permissions are validated through the same SQL views (`effective_task_access`)

#### 2. Information Disclosure
**Concern:** Could users with task-only access see project-level information they shouldn't?

**Analysis:** Safe because:
- Project listing shows projects where user has either project OR task access
- This is intentional - users need to see the project to access the task
- Task-level data is still protected by task-level permission checks
- Project-level admin functions still require project-level admin access

#### 3. Privilege Escalation
**Concern:** Could users escalate privileges by manipulating task permissions?

**Analysis:** No escalation possible because:
- Only site admins and project admins can set permissions
- Permission setting functions haven't changed their authorization checks
- Setting task permission doesn't grant project permission

#### 4. API and Anonymization Permissions
**Concern:** Are API and anonymization permissions properly enforced?

**Analysis:** These remain secure because:
- API permissions are still checked at project level (line 254 in auth.py)
- Anonymization permissions are managed at project level
- These are not affected by task-level access

### Security Validation

**CodeQL Analysis:** Passed with 0 vulnerabilities

**Manual Review:** 
- All access checks still occur
- No bypass mechanisms introduced
- Authorization hierarchy maintained
- Permissions still require admin privileges to modify

## Changes Made

### 1. Core Permission Logic (project_ref.py)

#### `user_set_access_level()` (lines 318-344)
**Before:** Automatically lowered task permissions when project permission was reduced
```python
# For each task, make sure its access level is <= that of the project access level
if access_level is not None:
    for row in rc1.fetchall():
        if AccessLevel.to_int(row['access']) > AccessLevel.to_int(access_level):
            db.execute('UPDATE task_access SET access=? WHERE task=? and user=?',
                    (access_level, row['task'], user))
```

**After:** Removed automatic lowering - task permissions are independent
```python
# Task permissions are now independent of project permissions
# No automatic lowering performed
```

#### `user_set_task_access_level()` (lines 360-397)
**Before:** Automatically elevated project permission when task permission was higher
```python
# Make sure the project access is at least as high as the task access
val_project = AccessLevel.to_int(row['access'])
if val_project < val_new:
    db.execute('UPDATE project_access SET access=? WHERE project=? AND user=?',
            (access_level, self.name, user))
```

**After:** Only ensures project_access record exists, defaults to 'none'
```python
# Ensure the user has a project access record (with 'none' if not present)
if row is None:
    db.execute('INSERT INTO project_access (user,project,access,anon_permission,api_permission) VALUES (?,?,?,0,0)',
               (user, self.name, "none"))
```

### 2. Access Control Logic (auth.py)

#### `_task_access_required()` (lines 237-267)
**Before:** Checked project_access first, then task_access as additional restriction
```python
if AccessLevel.check_access(tpa['project_access'], min_access_level) is False:
    error = "Insufficient privileges on parent project"
elif tpa['restrict_access'] > 0 and AccessLevel.check_access(tpa['task_access'], min_access_level) is False:
    error = "Insufficient privileges on task"
```

**After:** Checks task_access for restricted tasks, project_access for non-restricted
```python
if tpa['restrict_access'] > 0:
    # Task has specific access control - use task-level access
    if AccessLevel.check_access(tpa['task_access'], min_access_level) is False:
        error = "Insufficient privileges on task"
else:
    # Task does not have specific access control - use project-level access as wildcard
    if AccessLevel.check_access(tpa['project_access'], min_access_level) is False:
        error = "Insufficient privileges on parent project"
```

### 3. User Interface Updates

#### Project Listing (slide.py lines 86-98)
**Before:** Only showed projects where `access != "none"`
```python
WHERE PA.user = ? AND PA.access != "none"
```

**After:** Shows projects where user has project OR task access
```python
WHERE (
  (PA.access IS NOT NULL AND PA.access != "none") OR 
  (TI.restrict_access > 0 AND TPA.task_access IS NOT NULL AND TPA.task_access != "none")
)
```

#### CLI User Listing (project_cli.py)
**Updated:** Task user listing to check correct permissions based on restrict_access flag
**Updated:** Project listing for users to include projects with task-only access
**Updated:** Display logic to show task vs project access appropriately

### 4. Documentation (docs/source/permissions.rst)
**Added:** Comprehensive documentation explaining:
- Access levels
- Project vs task permissions
- Permission precedence rules
- Examples of different permission scenarios
- Security considerations

## Use Cases Enabled

### Use Case 1: Limited Task Access
**Scenario:** External collaborator should only access specific annotation task
- Project access: `none`
- Task "Annotate" (restrict_access=true): `write`
- Result: Can annotate but not browse other tasks

### Use Case 2: Read-Only Project with Write Task
**Scenario:** Student has general read access but can write to specific task
- Project access: `read`
- Task "Student Work" (restrict_access=true): `write`
- Result: Can browse all tasks, write to specific task

### Use Case 3: Project Wildcard
**Scenario:** Regular user has standard access across non-restricted tasks
- Project access: `read`
- No specific task permissions
- Result: Has read access to all non-restricted tasks

## Testing Recommendations

1. **Permission Setting:**
   - Set project=none, task=read → verify task is accessible
   - Set project=read, task=write → verify correct access levels
   - Set project=admin, task=read → verify task restriction works

2. **Project Listing:**
   - User with only task access should see the project
   - User with no project or task access should not see project

3. **Task Listing:**
   - Non-restricted tasks should use project access
   - Restricted tasks should use task access
   - Mixed scenarios should work correctly

4. **API Access:**
   - Verify API permissions still work at project level
   - Task access alone should not grant API access without project-level permission

## Conclusion

The new permission model provides the requested flexibility while maintaining security:
- Task permissions can now be higher than project permissions
- Project permissions act as wildcards for non-restricted tasks
- All access checks are still performed appropriately
- No security vulnerabilities introduced (verified by CodeQL)
- Clear documentation provided for users and administrators
