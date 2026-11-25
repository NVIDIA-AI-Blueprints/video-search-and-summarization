/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
* SPDX-License-Identifier: Apache-2.0
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/

/**
 * Project Setup Step
 * Create and manage calibration projects
 */

import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  TextField,
  Button,
  Card,
  CardContent,
  CardActions,
  Grid,
  Chip,
  Alert,
  CircularProgress,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Checkbox,
  FormControlLabel,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import RefreshIcon from '@mui/icons-material/Refresh';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import { useApp } from '../../context/AppContext';
import { api } from '../../services/api';
import type { Project } from '../../types';

interface ProjectSetupProps {
  onProjectSelect: (project: Project) => void;
}

const ProjectSetup: React.FC<ProjectSetupProps> = ({ onProjectSelect }) => {
  const { showSuccess, showError, showWarning, selectedProject, setSelectedProject } = useApp();
  const [projectName, setProjectName] = useState('');
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [projectToDelete, setProjectToDelete] = useState<Project | null>(null);
  const [deleteAllDialogOpen, setDeleteAllDialogOpen] = useState(false);
  const [deleteAllBusy, setDeleteAllBusy] = useState(false);
  /** Multi-select for bulk delete (independent of workflow "Select"). */
  const [checkedProjectIds, setCheckedProjectIds] = useState<Set<string>>(new Set());
  const [deleteSelectedDialogOpen, setDeleteSelectedDialogOpen] = useState(false);
  const [deleteSelectedBusy, setDeleteSelectedBusy] = useState(false);

  // Load projects on mount
  useEffect(() => {
    loadProjects();
  }, []);

  // Load all projects from backend
  const loadProjects = async () => {
    try {
      setLoading(true);
      const response = await api.project.listProjects();
      setProjects(response.projects);
      // Drop checks for projects that no longer exist
      setCheckedProjectIds((prev) => {
        const next = new Set<string>();
        const ids = new Set(response.projects.map((p) => p.project_id));
        prev.forEach((id) => {
          if (ids.has(id)) next.add(id);
        });
        return next;
      });
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to load projects';
      showError(errorMessage);
      console.error('Error loading projects:', error);
    } finally {
      setLoading(false);
    }
  };

  // Create new project
  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!projectName.trim()) {
      showError('Please enter a project name');
      return;
    }

    if (projectName.length < 3) {
      showError('Project name must be at least 3 characters');
      return;
    }

    try {
      setCreating(true);
      const response = await api.project.createProject({
        project_name: projectName.trim(),
      });
      
      showSuccess(response.message);
      setProjectName('');
      
      // Reload projects to show the new one
      await loadProjects();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to create project';
      showError(errorMessage);
      console.error('Error creating project:', error);
    } finally {
      setCreating(false);
    }
  };

  // Select a project
  const handleSelectProject = (project: Project) => {
    onProjectSelect(project);
    showSuccess(`Project "${project.project_name}" selected`);
  };

  const toggleProjectChecked = (projectId: string) => {
    setCheckedProjectIds((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (checkedProjectIds.size === projects.length) {
      setCheckedProjectIds(new Set());
    } else {
      setCheckedProjectIds(new Set(projects.map((p) => p.project_id)));
    }
  };

  // Open delete confirmation dialog
  const handleDeleteClick = (project: Project) => {
    setProjectToDelete(project);
    setDeleteDialogOpen(true);
  };

  // Delete project
  const handleDeleteConfirm = async () => {
    if (!projectToDelete) return;

    try {
      await api.project.deleteProject(projectToDelete.project_id);
      if (selectedProject?.project_id === projectToDelete.project_id) {
        setSelectedProject(null);
      }
      showSuccess('Project deleted successfully');
      setDeleteDialogOpen(false);
      setProjectToDelete(null);
      setCheckedProjectIds((prev) => {
        const next = new Set(prev);
        next.delete(projectToDelete.project_id);
        return next;
      });
      
      // Reload projects
      await loadProjects();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to delete project';
      showError(errorMessage);
      console.error('Error deleting project:', error);
    }
  };

  const handleDeleteSelectedConfirm = async () => {
    const ids = Array.from(checkedProjectIds);
    if (ids.length === 0) return;

    try {
      setDeleteSelectedBusy(true);
      const res = await api.project.deleteProjects(ids);
      const deletedIds = new Set(res.deleted_project_ids);
      setProjects(res.projects);
      setCheckedProjectIds(new Set());

      if (selectedProject && deletedIds.has(selectedProject.project_id)) {
        setSelectedProject(null);
      }

      setDeleteSelectedDialogOpen(false);

      const deleted = deletedIds.size;
      const failures = Object.entries(res.skipped_projects).map(([id, reason]) => {
        const name = projects.find((p) => p.project_id === id)?.project_name ?? id;
        return `${name}: ${reason}`;
      });
      if (res.code === 0) {
        showSuccess(res.message);
      } else if (deleted > 0) {
        showWarning(
          `Deleted ${deleted} project(s). ${failures.length} failed: ${failures.join('; ')}`,
        );
      } else {
        showError(`Could not delete selected projects: ${failures.join('; ')}`);
      }
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to delete selected projects');
      console.error('Error deleting selected projects:', error);
    } finally {
      setDeleteSelectedBusy(false);
    }
  };

  const handleDeleteAllConfirm = async () => {
    try {
      setDeleteAllBusy(true);
      const res = await api.project.deleteAllProjects();
      await loadProjects();
      setCheckedProjectIds(new Set());
      const remaining = new Set(res.projects.map((p) => p.project_id));
      if (selectedProject && !remaining.has(selectedProject.project_id)) {
        setSelectedProject(null);
      }
      if (res.code !== 0) {
        showWarning(res.message);
      } else {
        showSuccess(res.message);
      }
      setDeleteAllDialogOpen(false);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to delete projects');
      console.error('Error deleting all projects:', error);
    } finally {
      setDeleteAllBusy(false);
    }
  };

  // Get status color
  const getStateColor = (state: string) => {
    switch (state) {
      case 'INIT':
        return 'default';
      case 'READY':
        return 'primary';
      case 'RUNNING':
        return 'warning';
      case 'COMPLETED':
        return 'success';
      case 'ERROR':
        return 'error';
      default:
        return 'default';
    }
  };

  const checkedCount = checkedProjectIds.size;
  const allChecked = projects.length > 0 && checkedCount === projects.length;

  return (
    <Box>
      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h5">Project Setup</Typography>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <Button startIcon={<RefreshIcon />} onClick={loadProjects} disabled={loading}>
            Refresh
          </Button>
          {checkedCount > 0 && (
            <Button
              color="error"
              variant="contained"
              startIcon={<DeleteIcon />}
              disabled={loading || deleteSelectedBusy || deleteAllBusy}
              onClick={() => setDeleteSelectedDialogOpen(true)}
            >
              Delete selected ({checkedCount})
            </Button>
          )}
          <Button
            color="error"
            variant="outlined"
            startIcon={<DeleteIcon />}
            disabled={loading || projects.length === 0 || deleteAllBusy || deleteSelectedBusy}
            onClick={() => setDeleteAllDialogOpen(true)}
          >
            Delete all
          </Button>
        </Box>
      </Box>

      {/* Create Project Form */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Create New Project
          </Typography>
          <form onSubmit={handleCreateProject}>
            <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-start' }}>
              <TextField
                label="Project Name"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder="e.g., warehouse_calibration"
                fullWidth
                helperText="3-50 characters, letters, numbers, hyphens, and underscores only"
                disabled={creating}
              />
              <Button
                type="submit"
                variant="contained"
                disabled={creating || !projectName.trim()}
                sx={{ minWidth: 120, height: 56 }}
              >
                {creating ? <CircularProgress size={24} /> : 'Create'}
              </Button>
            </Box>
          </form>
        </CardContent>
      </Card>

      {/* Projects List */}
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          mb: 1,
          flexWrap: 'wrap',
          gap: 1,
        }}
      >
        <Typography variant="h6">Existing Projects</Typography>
        {projects.length > 0 && !loading && (
          <FormControlLabel
            control={
              <Checkbox
                checked={allChecked}
                indeterminate={checkedCount > 0 && !allChecked}
                onChange={toggleSelectAll}
                disabled={deleteSelectedBusy || deleteAllBusy}
              />
            }
            label={allChecked ? 'Deselect all' : 'Select all'}
          />
        )}
      </Box>

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
          <CircularProgress />
        </Box>
      ) : projects.length === 0 ? (
        <Alert severity="info">
          No projects yet. Create your first project above to get started!
        </Alert>
      ) : (
        <Grid container spacing={2}>
          {projects.map((project) => {
            const isChecked = checkedProjectIds.has(project.project_id);
            const isWorkflowSelected = selectedProject?.project_id === project.project_id;
            return (
              <Grid item xs={12} sm={6} md={4} key={project.project_id}>
                <Card
                  sx={{
                    border: isChecked ? 2 : isWorkflowSelected ? 2 : 1,
                    borderColor: isChecked
                      ? 'error.main'
                      : isWorkflowSelected
                        ? 'primary.main'
                        : 'divider',
                    position: 'relative',
                    bgcolor: isChecked ? 'action.selected' : 'background.paper',
                  }}
                >
                  <CardContent>
                    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.5 }}>
                      <Checkbox
                        checked={isChecked}
                        onChange={() => toggleProjectChecked(project.project_id)}
                        disabled={deleteSelectedBusy || deleteAllBusy}
                        inputProps={{
                          'aria-label': `Select ${project.project_name} for deletion`,
                        }}
                        sx={{ mt: -0.5, ml: -1 }}
                      />
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        {/* Workflow selected indicator */}
                        {isWorkflowSelected && (
                          <CheckCircleIcon
                            color="primary"
                            sx={{ position: 'absolute', top: 8, right: 8 }}
                          />
                        )}

                        <Typography variant="h6" gutterBottom noWrap>
                          {project.project_name}
                        </Typography>
                        
                        <Typography variant="body2" color="text.secondary" gutterBottom noWrap>
                          ID: {project.project_id}
                        </Typography>

                        <Box sx={{ mt: 2, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                          <Chip
                            label={project.project_state}
                            color={getStateColor(project.project_state)}
                            size="small"
                          />
                          <Chip
                            label={`${project.video_files_count} videos`}
                            size="small"
                            variant="outlined"
                          />
                        </Box>

                        <Box sx={{ mt: 1 }}>
                          <Typography variant="caption" color="text.secondary">
                            GT: {project.groundtruth_file ? '✓' : '✗'} | 
                            Layout: {project.layout_file ? '✓' : '✗'} | 
                            Alignment: {project.alignment_file ? '✓' : '✗'}
                          </Typography>
                        </Box>
                      </Box>
                    </Box>
                  </CardContent>

                  <CardActions sx={{ justifyContent: 'space-between' }}>
                    <Button
                      size="small"
                      variant={isWorkflowSelected ? 'contained' : 'outlined'}
                      onClick={() => handleSelectProject(project)}
                    >
                      {isWorkflowSelected ? 'Selected' : 'Select'}
                    </Button>
                    <IconButton
                      size="small"
                      color="error"
                      onClick={() => handleDeleteClick(project)}
                      disabled={deleteSelectedBusy || deleteAllBusy}
                    >
                      <DeleteIcon />
                    </IconButton>
                  </CardActions>
                </Card>
              </Grid>
            );
          })}
        </Grid>
      )}

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)}>
        <DialogTitle>Delete Project?</DialogTitle>
        <DialogContent>
          <Typography>
            Are you sure you want to delete project "{projectToDelete?.project_name}"? 
            This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleDeleteConfirm} color="error" variant="contained">
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={deleteSelectedDialogOpen}
        onClose={() => !deleteSelectedBusy && setDeleteSelectedDialogOpen(false)}
      >
        <DialogTitle>
          Delete {checkedCount} selected project{checkedCount === 1 ? '' : 's'}?
        </DialogTitle>
        <DialogContent>
          <Typography sx={{ mb: 1.5 }}>
            This permanently deletes the selected projects. Projects with calibration running or
            RTSP capture in progress will be skipped. This cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => setDeleteSelectedDialogOpen(false)}
            disabled={deleteSelectedBusy}
          >
            Cancel
          </Button>
          <Button
            onClick={() => void handleDeleteSelectedConfirm()}
            color="error"
            variant="contained"
            disabled={deleteSelectedBusy}
          >
            {deleteSelectedBusy ? <CircularProgress size={22} /> : 'Delete selected'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={deleteAllDialogOpen} onClose={() => !deleteAllBusy && setDeleteAllDialogOpen(false)}>
        <DialogTitle>Delete all projects?</DialogTitle>
        <DialogContent>
          <Typography>
            This permanently deletes every project that can be removed. Projects with calibration
            running or RTSP capture in progress are skipped. This cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteAllDialogOpen(false)} disabled={deleteAllBusy}>
            Cancel
          </Button>
          <Button onClick={() => void handleDeleteAllConfirm()} color="error" variant="contained" disabled={deleteAllBusy}>
            {deleteAllBusy ? <CircularProgress size={22} /> : 'Delete all'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ProjectSetup;
