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
 * Main Calibration Workflow Component
 * Manages the step-by-step calibration process
 */

import React, { useState, useEffect } from 'react';
import {
  Grid,
  Stepper,
  Step,
  StepLabel,
  Button,
  Box,
  Paper,
  Typography,
  Alert,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { WORKFLOW_STEPS, WorkflowStepId } from '../constants';
import { useApp } from '../context/AppContext';
import type { Project } from '../types';

// Import step components
import ProjectSetup from './steps/ProjectSetup';
import VideoConfiguration from './steps/VideoConfiguration';
import Parameters from './steps/Parameters';
import Rectification from './steps/Rectification';
import ManualAlignment from './steps/ManualAlignment';
import Execute from './steps/Execute';
import Results from './steps/Results';

// ============================================================================
// Step Content Component
// ============================================================================

interface StepContentProps {
  stepId: WorkflowStepId;
  selectedProject: Project | null;
  onProjectSelect: (project: Project) => void;
  onProjectUpdate: (project: Project) => void;
}

const StepContent: React.FC<StepContentProps> = ({
  stepId,
  selectedProject,
  onProjectSelect,
  onProjectUpdate,
}) => {
  // Render appropriate step component based on stepId
  switch (stepId) {
    case WorkflowStepId.PROJECT_SETUP:
      return (
        <Paper sx={{ p: 3, minHeight: 400 }}>
          <ProjectSetup onProjectSelect={onProjectSelect} />
        </Paper>
      );

    case WorkflowStepId.VIDEO_CONFIGURATION:
      if (!selectedProject) {
        return (
          <Alert severity="warning">Please select a project first</Alert>
        );
      }
      return (
        <Paper sx={{ p: 3, minHeight: 400 }}>
          <VideoConfiguration 
            project={selectedProject} 
            onProjectUpdate={onProjectUpdate} 
          />
        </Paper>
      );

    case WorkflowStepId.PARAMETERS:
      if (!selectedProject) {
        return (
          <Alert severity="warning">Please select a project first</Alert>
        );
      }
      return (
        <Paper sx={{ p: 3, minHeight: 400 }}>
          <Parameters 
            project={selectedProject} 
            onProjectUpdate={onProjectUpdate} 
          />
        </Paper>
      );

    case WorkflowStepId.RECTIFICATION:
      if (!selectedProject) {
        return <Alert severity="warning">Please select a project first</Alert>;
      }
      return (
        <Paper sx={{ p: 3, minHeight: 400 }}>
          <Rectification project={selectedProject} onProjectUpdate={onProjectUpdate} />
        </Paper>
      );

    case WorkflowStepId.MANUAL_ALIGNMENT:
      if (!selectedProject) {
        return (
          <Alert severity="warning">Please select a project first</Alert>
        );
      }
      return (
        <Paper sx={{ p: 3, minHeight: 400 }}>
          <ManualAlignment 
            project={selectedProject} 
            onProjectUpdate={onProjectUpdate} 
          />
        </Paper>
      );

    case WorkflowStepId.EXECUTE:
      if (!selectedProject) {
        return (
          <Alert severity="warning">Please select a project first</Alert>
        );
      }
      return (
        <Paper sx={{ p: 3, minHeight: 400 }}>
          <Execute 
            project={selectedProject} 
            onProjectUpdate={onProjectUpdate} 
          />
        </Paper>
      );

    case WorkflowStepId.RESULTS:
      if (!selectedProject) {
        return (
          <Alert severity="warning">Please select a project first</Alert>
        );
      }
      return (
        <Paper sx={{ p: 3, minHeight: 400 }}>
          <Results 
            project={selectedProject}
            onProjectUpdate={onProjectUpdate}
          />
        </Paper>
      );

    default:
      return (
        <Alert severity="error">Invalid step</Alert>
      );
  }
};

// ============================================================================
// Main Workflow Component
// ============================================================================

const CalibrationWorkflow: React.FC = () => {
  const theme = useTheme();
  const { selectedProject, setSelectedProject, currentStep, setCurrentStep } = useApp();
  
  const [activeStep, setActiveStep] = useState<number>(currentStep);

  // Sync local state with context
  useEffect(() => {
    setActiveStep(currentStep);
  }, [currentStep]);

  // Navigation handlers
  const handleNext = () => {
    const nextStep = Math.min(activeStep + 1, WORKFLOW_STEPS.length - 1);
    setActiveStep(nextStep);
    setCurrentStep(nextStep);
  };

  const handleBack = () => {
    const prevStep = Math.max(activeStep - 1, 0);
    setActiveStep(prevStep);
    setCurrentStep(prevStep);
  };

  const handleStepClick = (step: number) => {
    const hasVideos = (selectedProject?.video_files_count ?? 0) > 0;
    const linearMediaReady = selectedProject?.rectification_state === 'COMPLETED';
    // Steps 0–3 (through Rectification): need project + videos after setup.
    // Steps 4+ (Alignment onward): require completed linear media.
    if (
      step === 0 ||
      (selectedProject && ((step <= 3 && hasVideos) || (step >= 4 && linearMediaReady)))
    ) {
      setActiveStep(step);
      setCurrentStep(step);
    }
  };

  // Project handlers
  const handleProjectSelect = (project: Project) => {
    setSelectedProject(project);
  };

  const handleProjectUpdate = (project: Project) => {
    setSelectedProject(project);
  };

  // Check if can proceed to next step
  const canProceedToNextStep = (): boolean => {
    switch (activeStep) {
      case 0:
        return selectedProject !== null;
      case 1:
        // Video Configuration → Parameters
        return (selectedProject?.video_files_count ?? 0) > 0;
      case 2:
        // Parameters → Rectification
        return (selectedProject?.video_files_count ?? 0) > 0;
      case 3:
        // Rectification → Manual Alignment
        return selectedProject?.rectification_state === 'COMPLETED';
      case WORKFLOW_STEPS.length - 1:
        return false;
      default:
        return selectedProject !== null;
    }
  };

  // Get current step configuration
  const getCurrentStepConfig = () => {
    return WORKFLOW_STEPS[activeStep];
  };

  return (
    <Grid container spacing={3}>
      {/* Stepper */}
      <Grid item xs={12}>
        <Paper
          elevation={1}
          sx={{
            p: theme.spacing(2),
            mb: theme.spacing(3),
            borderRadius: theme.shape.borderRadius,
          }}
        >
          <Stepper activeStep={activeStep} alternativeLabel>
            {WORKFLOW_STEPS.map((step, index) => (
              <Step key={step.id}>
                <StepLabel
                  sx={{
                    cursor: index === 0 || selectedProject ? 'pointer' : 'not-allowed',
                    opacity: index === 0 || selectedProject ? 1 : 0.5,
                    '& .MuiStepLabel-label': {
                      '&:hover': {
                        color:
                          index === 0 || selectedProject
                            ? theme.palette.primary.main
                            : 'inherit',
                        fontWeight:
                          index === 0 || selectedProject
                            ? theme.typography.fontWeightBold
                            : 'inherit',
                      },
                    },
                  }}
                  onClick={() => handleStepClick(index)}
                >
                  {step.label}
                </StepLabel>
              </Step>
            ))}
          </Stepper>
        </Paper>
      </Grid>

      {/* Step Description */}
      <Grid item xs={12}>
        <Box sx={{ mb: theme.spacing(2) }}>
          <Typography variant="body2" color="text.secondary">
            {getCurrentStepConfig().description}
          </Typography>
        </Box>
      </Grid>

      {/* Step Content */}
      <Grid item xs={12}>
        <StepContent
          stepId={WORKFLOW_STEPS[activeStep].id}
          selectedProject={selectedProject}
          onProjectSelect={handleProjectSelect}
          onProjectUpdate={handleProjectUpdate}
        />
      </Grid>

      {/* Navigation Buttons */}
      <Grid item xs={12}>
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            pt: theme.spacing(2),
            gap: theme.spacing(2),
          }}
        >
          <Button
            disabled={activeStep === 0}
            onClick={handleBack}
            variant="outlined"
            sx={{ minWidth: theme.spacing(12) }}
          >
            Previous
          </Button>

          <Box
            sx={{
              flex: 1,
              mx: theme.spacing(2),
              display: 'flex',
              justifyContent: 'center',
            }}
          >
            {activeStep === 0 && !selectedProject && (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{
                  alignSelf: 'center',
                  fontWeight: theme.typography.fontWeightMedium,
                }}
              >
                Please select or create a project to continue
              </Typography>
            )}
            {activeStep === 0 && selectedProject && (
              <Typography
                variant="body2"
                color="success.main"
                sx={{
                  alignSelf: 'center',
                  fontWeight: theme.typography.fontWeightMedium,
                }}
              >
                Project "{selectedProject.project_name}" selected
              </Typography>
            )}
          </Box>

          {activeStep < WORKFLOW_STEPS.length - 1 && (
            <Button
              disabled={!canProceedToNextStep()}
              onClick={handleNext}
              variant="contained"
              sx={{
                minWidth: theme.spacing(12),
                fontWeight: theme.typography.fontWeightMedium,
              }}
            >
              Next
            </Button>
          )}
        </Box>
      </Grid>
    </Grid>
  );
};

export default CalibrationWorkflow;
