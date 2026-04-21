// SPDX-License-Identifier: MIT
import { useState, useCallback, useEffect } from 'react';
import { FilterProps, SearchParams, StreamInfo } from '../types';
import { formatDatetime } from '../utils/Formatter';

// Centralized default constant - exported for use in other components
export const DEFAULT_TOP_K = 10;

export const useFilter = ({vstApiUrl}: FilterProps) => {
  const [streams, setStreams] = useState<StreamInfo[]>([]);
  const [filterParams, setFilterParams] = useState({
    startDate: null,
    endDate: null,
    videoSources: [],
    similarity: 0,
    agentMode: false,
    query: '',
    topK: DEFAULT_TOP_K
  })
  const [filterTags, setFilterTags] = useState([{key: 'topK', title: 'Show top K Results', value: DEFAULT_TOP_K.toString()}]);

  const fetchSensorList = useCallback(async () => {
    if (!vstApiUrl) return;
    
    try {
      const response = await fetch(`${vstApiUrl}/v1/sensor/list`);
      if (!response.ok) {
        console.error(`Failed to fetch sensor list: ${response.status}`);
        return;
      }
      const sensors = await response.json();
      
      const streamList: StreamInfo[] = [];
      sensors.forEach((sensor: any) => {
        if (sensor.name && sensor.sensorId && sensor.state === 'online') {
          streamList.push({
            name: sensor.name,
            type: sensor.type || ''
          });
        }
      });
      setStreams(streamList);
    } catch (err) {
      console.error('Error fetching sensor list:', err);
    }
  }, [vstApiUrl]);

  const addFilter = (params?: any) => {
    const paramsToUse = params || filterParams;
    const { startDate, endDate, videoSources, similarity, topK } = paramsToUse;
      
    let tags = [];
    if (startDate) {
      tags.push({key: 'startDate', title: 'From', value: formatDatetime(startDate)});
    } 
    if (endDate) {
      tags.push({key: 'endDate', title: 'To', value: formatDatetime(endDate)});
    }
    if (videoSources && videoSources.length > 0) {
      tags.push({key: 'videoSources', title: 'Video sources', value: videoSources.join(', ')});
    }
    if (similarity) {
      tags.push({key: 'similarity', title: 'Similarity', value: Number(similarity)?.toFixed(2)});
    }
    // Always include topK tag (robust to numeric 0 or other non-truthy but valid numbers)
    if (topK !== undefined && topK !== null) {
      tags.push({key: 'topK', title: 'Show top K Results', value: topK.toString()});
    }
    setFilterTags(tags as any);
  };

  const removeFilterTag = (tag: any) => {
    if (!tag) {
      setFilterTags([{key: 'topK', title: 'Show top K Results', value: (filterParams.topK ?? DEFAULT_TOP_K).toString()}]);
    } else {
      setFilterTags(filterTags.filter((t: any) => t !== tag));
    }
  };

  const fetchData = useCallback(async () => {
    await fetchSensorList();
  }, [fetchSensorList]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return {
    streams,
    filterParams,
    setFilterParams,
    refetch: fetchData,
    addFilter,
    filterTags,
    removeFilterTag
  };
};