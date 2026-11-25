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

import { useState, useMemo } from 'react';
import { Container, Box, AppBar, Toolbar, Typography, Snackbar, Alert, IconButton } from '@mui/material';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import CssBaseline from '@mui/material/CssBaseline';
import CameraAltIcon from '@mui/icons-material/CameraAlt';
import SettingsIcon from '@mui/icons-material/Settings';
import Brightness4Icon from '@mui/icons-material/Brightness4';
import Brightness7Icon from '@mui/icons-material/Brightness7';
import { AppProvider, useApp } from './context/AppContext';
import CalibrationWorkflow from './components/CalibrationWorkflow';
import Settings from './components/Settings';
import { UI_CONFIG, APP_CONFIG } from './config';
import { WORKFLOW_STEPS, WorkflowStepId } from './constants';



interface AppContentProps {
  mode: 'light' | 'dark';
  toggleTheme: () => void;
}

function AppContent({ mode, toggleTheme }: AppContentProps) {
  const { snackbar, hideSnackbar, currentStep } = useApp();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const isParametersStep = WORKFLOW_STEPS[currentStep]?.id === WorkflowStepId.PARAMETERS;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      {/* App Bar */}
      <AppBar position="static" elevation={1}>
        <Toolbar>
          <CameraAltIcon sx={{ mr: 2 }} />
          <Typography variant="h6" component="div" sx={{ flexGrow: 1 }}>
            {APP_CONFIG.name}
          </Typography>
          <Typography variant="body2" color="inherit" sx={{ opacity: 0.8, mr: 2 }}>
            v{APP_CONFIG.version}
          </Typography>
          <IconButton
            color="inherit"
            onClick={toggleTheme}
            title={mode === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
          >
            {mode === 'dark' ? <Brightness7Icon /> : <Brightness4Icon />}
          </IconButton>
        </Toolbar>
      </AppBar>

      {/* Parameters Settings Strip */}
      {isParametersStep && (
        <Box
          sx={{
            px: 3,
            py: 1.25,
            borderBottom: 1,
            borderColor: 'divider',
          }}
        >
          <Container
            maxWidth="xl"
            sx={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 1 }}
          >
            <Typography variant="body2" color="text.secondary">
              Click the icon to customize calibration parameters
            </Typography>
            <IconButton
              color="primary"
              onClick={() => setSettingsOpen(true)}
              title="Settings"
            >
              <SettingsIcon />
            </IconButton>
          </Container>
        </Box>
      )}

      {/* Main Content */}
      <Container maxWidth="xl" sx={{ mt: 4, mb: 4, flex: 1 }}>
        <CalibrationWorkflow />
      </Container>

      {/* Footer */}
      <Box
        component="footer"
        sx={{
          py: 2,
          px: 2,
          mt: 'auto',
          backgroundColor: (theme) => 
            theme.palette.mode === 'dark' ? theme.palette.grey[900] : theme.palette.grey[100],
        }}
      >
        <Container maxWidth="xl">
          <Typography variant="body2" color="text.secondary" align="center">
            {APP_CONFIG.name} © {new Date().getFullYear()} {APP_CONFIG.organization}
          </Typography>
        </Container>
      </Box>

      {/* Snackbar for notifications */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={
          snackbar.severity === 'warning'
            ? UI_CONFIG.warningSnackbarDuration
            : UI_CONFIG.snackbarDuration
        }
        onClose={hideSnackbar}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert onClose={hideSnackbar} severity={snackbar.severity} sx={{ width: '100%' }}>
          {snackbar.message}
        </Alert>
      </Snackbar>

      {/* Settings Dialog */}
      <Settings open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </Box>
  );
}

function App() {
  const [mode, setMode] = useState<'light' | 'dark'>('light');

  const toggleTheme = () => {
    setMode((prevMode) => (prevMode === 'light' ? 'dark' : 'light'));
  };

  const theme = useMemo(
    () =>
      createTheme({
        palette: {
          mode,
          primary: {
            main: UI_CONFIG.primaryColor,
          },
          secondary: {
            main: UI_CONFIG.secondaryColor,
          },
        },
        typography: {
          fontFamily: '"Roboto", "Helvetica", "Arial", sans-serif',
        },
      }),
    [mode]
  );

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AppProvider>
        <AppContent mode={mode} toggleTheme={toggleTheme} />
      </AppProvider>
    </ThemeProvider>
  );
}

export default App;
