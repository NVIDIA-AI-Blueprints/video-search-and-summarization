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
import React from 'react';
import { Box, Tabs, Tab } from '@mui/material';
import DebugOptions from '../../components/debugOptions/DebugOptions';
import ChatBot from '../../features/chatBot/ChatBot';
import StreamStats from './experimentalPages/StreamStats';
import SystemStats from './experimentalPages/SystemStats';
import TokkioExperience from './experimentalPages/TokkioExperience';
import RTSPStreamQOS from './experimentalPages/RTSPStreamQOS';
import PictureAutomation from './experimentalPages/PictureAutomation';
import { StreamAutomation } from './experimentalPages/StreamAutomation';
import VideoURL from './experimentalPages/VideoURL';
import PutFileUpload from './experimentalPages/PutFileUpload';

// Define the type for the tab items
type TabItem = {
    label: string;
    content: React.ReactNode;
};

// Define the props for the ExperimentalTabs component
interface AppProps {
    tabs: TabItem[];
    defaultActiveTab?: number;
}

// Define the props for the TabPanel component
interface TabPanelProps {
    children?: React.ReactNode;
    value: number;
    index: number;
}

const TabPanel: React.FC<TabPanelProps> = ({ children, value, index }) => (
    <div
        role='tabpanel'
        hidden={value !== index}
        id={`tabpanel-${index}`}
        aria-labelledby={`tab-${index}`}
        style={{
            display: value === index ? 'block' : 'none',
        }}
    >
        {children}
    </div>
);

const ExperimentalTabs: React.FC<AppProps> = ({ tabs, defaultActiveTab = 0 }) => {
    const [activeTab, setActiveTab] = React.useState<number>(defaultActiveTab);

    const handleTabChange = (_event: React.SyntheticEvent, newValue: number) => {
        setActiveTab(newValue);
    };

    return (
        <Box sx={{ width: '100%' }}>
            <Tabs value={activeTab} onChange={handleTabChange} variant='scrollable' scrollButtons='auto'>
                {tabs.map((tab, index) => (
                    <Tab key={index} label={tab.label} id={`tab-${index}`} aria-controls={`tabpanel-${index}`} />
                ))}
            </Tabs>
            <Box sx={{ padding: 2 }}>
                {tabs.map((tab, index) => (
                    <TabPanel key={index} value={activeTab} index={index}>
                        {tab.content}
                    </TabPanel>
                ))}
            </Box>
        </Box>
    );
};

const Experimental: React.FC = () => {
    const tabs: TabItem[] = [
        { label: 'Tokkio Experience', content: <TokkioExperience /> },
        { label: 'Stream Stats', content: <StreamStats /> },
        { label: 'System Stats', content: <SystemStats /> },
        { label: 'RTSP Stream QOS', content: <RTSPStreamQOS /> },
        { label: 'Debug Options', content: <DebugOptions /> },
        { label: 'Chat Bot', content: <ChatBot /> },
        {
            label: 'Picture Automation',
            content: <PictureAutomation />,
        },
        {
            label: 'Playback Automation',
            content: <StreamAutomation />,
        },
        {
            label: 'Media URL',
            content: <VideoURL />,
        },
        {
            label: 'PUT File Upload',
            content: <PutFileUpload />,
        },
    ];

    return <ExperimentalTabs tabs={tabs} />;
};

export default Experimental;
