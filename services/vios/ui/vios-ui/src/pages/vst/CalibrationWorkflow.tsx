/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import React, { useState } from 'react';
import { Grid2 as Grid, Stepper, Step, StepLabel, Button, Box, Paper, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import {
    ProjectSelection,
    ProjectDetails,
    MmsURLConfiguration,
    SetupSensors,
    Calibration,
    Validation,
    CalibrationDataManagement,
    Project,
} from './calibration-steps';

const getStepsForProjectType = (projectType?: string) => {
    if (projectType === 'image') {
        return [
            'Step 1: Project Selection',
            'Step 2: VST URL Configuration',
            'Step 3: Setup Sensors',
            'Step 4: Calibration',
            'Step 5: JSON Data Management',
        ];
    }

    // Default steps for non-image projects
    return [
        'Step 1: Project Selection',
        'Step 2: Project Details',
        'Step 3: VST URL Configuration',
        'Step 4: Setup Sensors',
        'Step 5: Calibration',
        'Step 6: Validation',
        'Step 7: JSON Data Management',
    ];
};

const getStepContentForProjectType = (step: number, projectType?: string) => {
    if (projectType === 'image') {
        // Map steps for image projects
        switch (step) {
            case 0:
                return 'project-selection';
            case 1:
                return 'mms-url-configuration';
            case 2:
                return 'setup-sensors';
            case 3:
                return 'calibration';
            case 4:
                return 'json-data-management';
            default:
                return null;
        }
    }

    // Default mapping for non-image projects
    switch (step) {
        case 0:
            return 'project-selection';
        case 1:
            return 'project-details';
        case 2:
            return 'mms-url-configuration';
        case 3:
            return 'setup-sensors';
        case 4:
            return 'calibration';
        case 5:
            return 'validation';
        case 6:
            return 'json-data-management';
        default:
            return null;
    }
};

const StepContent = ({
    step,
    selectedProject,
    onProjectSelect,
    onProjectUpdate,
    onProjectsLoaded,
}: {
    step: number;
    selectedProject?: Project;
    onProjectSelect: (project: Project) => void;
    onProjectUpdate: (project: Project) => void;
    onProjectsLoaded?: (projects: Project[]) => void;
}) => {
    const stepType = getStepContentForProjectType(step, selectedProject?.calibrationType);

    switch (stepType) {
        case 'project-selection':
            return (
                <ProjectSelection
                    onProjectSelect={onProjectSelect}
                    onProjectsLoaded={onProjectsLoaded}
                    selectedProjectId={selectedProject?.id}
                />
            );
        case 'project-details':
            return selectedProject ? <ProjectDetails projectId={selectedProject.id} onProjectUpdated={onProjectUpdate} /> : null;
        case 'mms-url-configuration':
            return selectedProject ? <MmsURLConfiguration projectId={selectedProject.id} onProjectUpdated={onProjectUpdate} /> : null;
        case 'setup-sensors':
            return selectedProject ? <SetupSensors projectId={selectedProject.id} onProjectUpdated={onProjectUpdate} /> : null;
        case 'calibration':
            return selectedProject ? <Calibration projectId={selectedProject.id} onProjectUpdated={onProjectUpdate} /> : null;
        case 'validation':
            return selectedProject ? <Validation projectId={selectedProject.id} onProjectUpdated={onProjectUpdate} /> : null;
        case 'json-data-management':
            return selectedProject ? (
                <CalibrationDataManagement project={selectedProject} selectedSensorId={selectedProject.sensor_set?.[0]?.id || null} />
            ) : null;
        default:
            return null;
    }
};

const CalibrationWorkflow = () => {
    const [activeStep, setActiveStep] = useState(0);
    const [selectedProject, setSelectedProject] = useState<Project | undefined>(undefined);
    const theme = useTheme();

    const steps = getStepsForProjectType(selectedProject?.calibrationType);
    const maxSteps = steps.length;

    const handleNext = () => {
        setActiveStep(prevActiveStep => Math.min(prevActiveStep + 1, maxSteps - 1));
    };

    const handleBack = () => {
        setActiveStep(prevActiveStep => Math.max(prevActiveStep - 1, 0));
    };

    const handleStepClick = (step: number) => {
        // Only allow navigation if a project is selected
        if (selectedProject) {
            setActiveStep(step);
        }
    };

    const handleProjectSelect = (project: Project) => {
        setSelectedProject(project);
        // Reset to first step when project type changes
        setActiveStep(0);
    };

    const handleProjectsLoaded = (projects: Project[]) => {
        // Check if the currently selected project still exists in the updated list
        if (selectedProject) {
            const projectStillExists = projects.some(project => project.id === selectedProject.id);
            if (!projectStillExists) {
                // Clear selection if the selected project was deleted
                setSelectedProject(undefined);
                setActiveStep(0);
                return;
            }
        }

        // Auto-select first project if no project is currently selected and projects are available
        if (!selectedProject && projects.length > 0) {
            handleProjectSelect(projects[0]);
        }
    };

    const canProceedToNextStep = () => {
        switch (activeStep) {
            case 0:
                return selectedProject !== undefined;
            case maxSteps - 1:
                // Last step - can't proceed further
                return false;
            default:
                return true;
        }
    };

    return (
        <Grid container spacing={3}>
            <Grid size={{ xs: 12 }}>
                <Paper
                    elevation={1}
                    sx={{
                        p: theme.spacing(2),
                        mb: theme.spacing(3),
                        borderRadius: theme.shape.borderRadius,
                    }}
                >
                    <Stepper activeStep={activeStep} alternativeLabel>
                        {steps.map((label, index) => (
                            <Step key={label}>
                                <StepLabel
                                    sx={{
                                        cursor: selectedProject ? 'pointer' : 'not-allowed',
                                        opacity: selectedProject ? 1 : 0.5,
                                        '& .MuiStepLabel-label': {
                                            '&:hover': {
                                                color: selectedProject ? theme.palette.primary.main : 'inherit',
                                                fontWeight: selectedProject ? theme.typography.fontWeightBold : 'inherit',
                                            },
                                        },
                                    }}
                                    onClick={() => handleStepClick(index)}
                                >
                                    {label}
                                </StepLabel>
                            </Step>
                        ))}
                    </Stepper>
                </Paper>
            </Grid>

            <Grid size={{ xs: 12 }}>
                <StepContent
                    step={activeStep}
                    selectedProject={selectedProject}
                    onProjectSelect={handleProjectSelect}
                    onProjectUpdate={setSelectedProject}
                    onProjectsLoaded={handleProjectsLoaded}
                />
            </Grid>

            <Grid size={{ xs: 12 }}>
                <Box
                    sx={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        pt: theme.spacing(2),
                        gap: theme.spacing(2),
                    }}
                >
                    <Button disabled={activeStep === 0} onClick={handleBack} variant='outlined' sx={{ minWidth: theme.spacing(12) }}>
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
                                variant='body2'
                                color='text.secondary'
                                sx={{
                                    alignSelf: 'center',
                                    fontWeight: theme.typography.fontWeightMedium,
                                }}
                            >
                                Please select a project to continue
                            </Typography>
                        )}
                        {activeStep === 0 && selectedProject && (
                            <Typography
                                variant='body2'
                                color='success.main'
                                sx={{
                                    alignSelf: 'center',
                                    fontWeight: theme.typography.fontWeightMedium,
                                }}
                            >
                                Project "{selectedProject.name}" selected
                                {selectedProject.calibrationType === 'image' && (
                                    <Typography
                                        component='span'
                                        variant='body2'
                                        color='info.main'
                                        sx={{
                                            ml: theme.spacing(1),
                                            fontWeight: theme.typography.fontWeightRegular,
                                        }}
                                    >
                                        (Streamlined workflow for image projects)
                                    </Typography>
                                )}
                            </Typography>
                        )}
                    </Box>
                    <Button
                        disabled={activeStep === maxSteps - 1 || !canProceedToNextStep()}
                        onClick={handleNext}
                        variant='contained'
                        sx={{
                            minWidth: theme.spacing(12),
                            fontWeight: theme.typography.fontWeightMedium,
                        }}
                    >
                        {activeStep === maxSteps - 1 ? 'Complete' : 'Next'}
                    </Button>
                </Box>
            </Grid>
        </Grid>
    );
};

export default CalibrationWorkflow;
