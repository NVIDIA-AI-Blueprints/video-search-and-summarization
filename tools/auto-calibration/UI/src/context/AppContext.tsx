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
 * Application context for managing global state
 * All types are properly defined - no 'any' types
 */

import React, { createContext, useContext, useState, useCallback, useEffect, ReactNode } from 'react';
import type { Project, ApiError } from '../types';

// ============================================================================
// Context Types
// ============================================================================

interface AppContextType {
  // System Capabilities
  vggtAvailable: boolean;
  rtspSupported: boolean;
  
  // Selected Project
  selectedProject: Project | null;
  setSelectedProject: (project: Project | null) => void;

  // Projects List
  projects: Project[];
  setProjects: (projects: Project[]) => void;

  // Loading States
  isLoading: boolean;
  setIsLoading: (loading: boolean) => void;

  // Error State
  error: ApiError | null;
  setError: (error: ApiError | null) => void;

  // Snackbar State
  snackbar: SnackbarState;
  showSuccess: (message: string) => void;
  showError: (message: string) => void;
  showInfo: (message: string) => void;
  showWarning: (message: string) => void;
  hideSnackbar: () => void;

  // Workflow State
  currentStep: number;
  setCurrentStep: (step: number) => void;

  // Calibration elapsed (persists across step navigation)
  calibrationStartTime: Record<string, number>;
  setCalibrationStartTime: (projectId: string, timestamp: number | null) => void;

  // Helper Functions
  refreshProject: (projectId: string) => Promise<void>;
  refreshProjects: () => Promise<void>;
}

interface SnackbarState {
  open: boolean;
  message: string;
  severity: 'success' | 'error' | 'info' | 'warning';
}

// ============================================================================
// Context Creation
// ============================================================================

const AppContext = createContext<AppContextType | undefined>(undefined);

// ============================================================================
// Provider Component
// ============================================================================

interface AppProviderProps {
  children: ReactNode;
}

export const AppProvider: React.FC<AppProviderProps> = ({ children }) => {
  // System State
  const [vggtAvailable, setVggtAvailable] = useState<boolean>(false);
  const [rtspSupported, setRtspSupported] = useState<boolean>(false);
  
  // State
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [currentStep, setCurrentStep] = useState<number>(0);
  const [calibrationStartTime, setCalibrationStartTimeState] = useState<Record<string, number>>({});
  const setCalibrationStartTime = useCallback((projectId: string, timestamp: number | null) => {
    setCalibrationStartTimeState((prev) => {
      if (timestamp === null) {
        const next = { ...prev };
        delete next[projectId];
        return next;
      }
      return { ...prev, [projectId]: timestamp };
    });
  }, []);
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'info',
  });

  // Snackbar Functions
  const showSuccess = useCallback((message: string) => {
    setSnackbar({ open: true, message, severity: 'success' });
  }, []);

  const showError = useCallback((message: string) => {
    setSnackbar({ open: true, message, severity: 'error' });
  }, []);

  const showInfo = useCallback((message: string) => {
    setSnackbar({ open: true, message, severity: 'info' });
  }, []);

  const showWarning = useCallback((message: string) => {
    setSnackbar({ open: true, message, severity: 'warning' });
  }, []);

  const hideSnackbar = useCallback(() => {
    setSnackbar((prev) => ({ ...prev, open: false }));
  }, []);

  // Project Management Functions
  const refreshProject = useCallback(
    async (projectId: string) => {
      // This will be implemented by the consuming component
      // using the API service
      console.log('Refresh project:', projectId);
    },
    []
  );

  const refreshProjects = useCallback(async () => {
    // This will be implemented by the consuming component
    // using the API service
    console.log('Refresh projects');
  }, []);

  // Load system capabilities on mount
  useEffect(() => {
    const loadCapabilities = async () => {
      try {
        const { api } = await import('../services/api');
        const response = await api.system.getCapabilities();
        setVggtAvailable(response.vggt_available);
        setRtspSupported(response.rtsp_supported !== false);
      } catch (error) {
        console.error('Failed to load system capabilities:', error);
        // Default to false if fetch fails
        setVggtAvailable(false);
        setRtspSupported(true);
      }
    };
    
    loadCapabilities();
  }, []);

  // Context Value
  const value: AppContextType = {
    vggtAvailable,
    rtspSupported,
    selectedProject,
    setSelectedProject,
    projects,
    setProjects,
    isLoading,
    setIsLoading,
    error,
    setError,
    snackbar,
    showSuccess,
    showError,
    showInfo,
    showWarning,
    hideSnackbar,
    currentStep,
    setCurrentStep,
    calibrationStartTime,
    setCalibrationStartTime,
    refreshProject,
    refreshProjects,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
};

// ============================================================================
// Hook for using context
// ============================================================================

export const useApp = (): AppContextType => {
  const context = useContext(AppContext);
  if (context === undefined) {
    throw new Error('useApp must be used within an AppProvider');
  }
  return context;
};

export default AppContext;

