// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { useState, useCallback, useRef } from "react";
import PropTypes from "prop-types";
import { GoogleMap, useLoadScript } from "@react-google-maps/api";
import { OverlappingMarkerSpiderfier } from "ts-overlapping-marker-spiderfier";
import _ from "lodash";

import LoaderDisplay from "./LoaderDisplay/LoaderDisplay";
import UserMessage from "../../utils/UserMessage";

import DefaultMapStyle from "./DefaultMapStyle.json";
import "./MapPulseAnimation.css";

const DefaultLoadingUserMessage =
  "Loading. Check console logs if loading continues for long";
const DefaultLibraries = ["drawing"];

function MapPlacesFallback({ children }) {
  const items = React.Children.toArray(children).filter(React.isValidElement);
  return (
    <div className="flex h-full w-full flex-col gap-3 overflow-auto bg-neutral-100 p-4 dark:bg-neutral-950">
      <p className="text-sm text-neutral-600 dark:text-neutral-300">
        Google Maps is unavailable without NEXT_PUBLIC_GOOGLE_MAPS_API_KEY.
        Select a location to continue.
      </p>
      <div className="flex flex-wrap gap-2">
        {items.map((child, index) => {
          const title = child.props?.title || `Place ${index + 1}`;
          const label = String(title).split("\n")[0];
          return (
            <button
              key={child.key || index}
              type="button"
              className="rounded border border-neutral-400 bg-white px-3 py-2 text-left text-sm hover:border-[#76b900] dark:border-neutral-600 dark:bg-neutral-900 dark:text-neutral-100"
              onClick={() => child.props?.onLeftClick?.()}
              onContextMenu={(event) => {
                event.preventDefault();
                child.props?.onRightClick?.();
              }}
              title={title}
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// Global flag to track if any Map component is mounted
let mapMountCount = 0;
let globalContextMenuHandler = null;
let globalMouseUpHandler = null;

// Set up global context menu prevention when first Map mounts
const setupGlobalContextMenuPrevention = () => {
  if (mapMountCount === 0) {
    if (!globalContextMenuHandler) {
      globalContextMenuHandler = (e) => {
        // Prevent context menu while any map is mounted
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        return false;
      };
      document.addEventListener('contextmenu', globalContextMenuHandler, { capture: true });
      window.addEventListener('contextmenu', globalContextMenuHandler, { capture: true });
    }
    
    if (!globalMouseUpHandler) {
      globalMouseUpHandler = (e) => {
        // Prevent default on right-click mouseup (button 2)
        if (e.button === 2) {
          e.preventDefault();
        }
      };
      document.addEventListener('mouseup', globalMouseUpHandler, { capture: true });
    }
  }
  mapMountCount++;
};

const teardownGlobalContextMenuPrevention = () => {
  mapMountCount--;
  if (mapMountCount === 0) {
    // Delay removal to ensure context menu is blocked even during view transitions
    setTimeout(() => {
      // Only remove if still at 0 (no new maps mounted)
      if (mapMountCount === 0) {
        if (globalContextMenuHandler) {
          document.removeEventListener('contextmenu', globalContextMenuHandler, { capture: true });
          window.removeEventListener('contextmenu', globalContextMenuHandler, { capture: true });
          globalContextMenuHandler = null;
        }
        if (globalMouseUpHandler) {
          document.removeEventListener('mouseup', globalMouseUpHandler, { capture: true });
          globalMouseUpHandler = null;
        }
      }
    }, 500); // 500ms delay to cover view transition
  }
};
/**
 * Wrap marker's setPosition and setOptions to prevent React re-renders from
 * resetting the position that OMS assigned during spiderfying. Icon/title/other
 * property changes still pass through; only position is held back while the
 * marker is in the spiderfied state.
 */
const _wrapMarkerPositionMethods = (marker) => {
  if (marker._positionMethodsWrapped) return;
  marker._positionMethodsWrapped = true;

  const origSetPosition = marker.setPosition.bind(marker);
  marker._origSetPosition = origSetPosition;

  marker.setPosition = function (pos) {
    if (this._spiderPositionProtected) {
      this._pendingPosition = pos;
      return;
    }
    return origSetPosition(pos);
  };

  if (typeof marker.setOptions === 'function') {
    const origSetOptions = marker.setOptions.bind(marker);
    marker._origSetOptions = origSetOptions;

    marker.setOptions = function (options) {
      if (this._spiderPositionProtected && options && ('position' in options)) {
        const { position, ...rest } = options;
        this._pendingPosition = position;
        if (Object.keys(rest).length > 0) {
          return origSetOptions(rest);
        }
        return;
      }
      return origSetOptions(options);
    };
  }
};

/**
 * Component wrapped around React based GoogleMap component.
 *
 * Accepts few props of 'useLoadScript' and all props of 'GoogleMap' from @react-google-maps/api
 * (https://react-google-maps-api-docs.netlify.app)
 *
 * Uses JS Object merging to merge with default properties set in this project.
 */
export default function Map(props) {
  if (!props.apiKey) {
    return <MapPlacesFallback>{props.children}</MapPlacesFallback>;
  }
  return <LoadedGoogleMap {...props} />;
}

function LoadedGoogleMap(props) {
  let {
    apiKey,
    libraries,
    name,
    version,
    mapContainerStyle,
    options,
    enableSpiderfier = false, // Enable/disable spiderfier
    spiderfierOptions = {},   // Custom spiderfier options
    onLoad: parentOnLoad,  // Extract onLoad to prevent override
    ...otherProps
  } = props;
  const usrMsgPurpose = "Load Map" + (name ? "(" + name + ")" : "");

  libraries = libraries || DefaultLibraries;
  version = version || "quarterly";

  const [oms, setOms] = useState(null);
  const pendingMarkers = useRef([]); // Array of { marker, handler } objects
  const allTrackedMarkers = useRef([]); // Keep track of all markers for overlap detection
  const mapListeners = useRef([]); // Keep track of all map event listeners for cleanup
  const mapIsIdle = useRef(false); // Track if map is ready for OMS operations
  const mapDivRef = useRef(null); // Reference to map div for context menu prevention
  const containerRef = useRef(null); // React ref for the wrapper container
  const { isLoaded, loadError } = useLoadScript({
    googleMapsApiKey: apiKey,
    libraries: libraries,
    version: version,
  });

  const defaultSpiderfierOptions = {
    markersWontMove: true,
    markersWontHide: true,
    basicFormatEvents: true,
    keepSpiderfied: false,
    nearbyDistance: 20,
    circleFootSeparation: 80,   // Increase spacing to avoid overlap
    spiralFootSeparation: 80,   // Increase spiral spacing
    spiralLengthStart: 15,      // Start spiral further out
    spiralLengthFactor: 6,      // Make spiral grow faster
    legWeight: 2                // Thicker lines for better visibility
  };

  // Helper: Add pulse class to marker element
  const _addPulseClass = (marker) => {
    if (marker.content && marker.content.classList) {
      marker.content.classList.add('overlapping-marker-pulse');
      return true;
    }
    if (marker.getElement && typeof marker.getElement === 'function') {
      const element = marker.getElement();
      if (element && element.classList) {
        element.classList.add('overlapping-marker-pulse');
        return true;
      }
    }
    return false;
  };

  // Helper: Remove pulse class from marker element
  const _removePulseClass = (marker) => {
    if (marker.content && marker.content.classList) {
      marker.content.classList.remove('overlapping-marker-pulse');
      return true;
    }
    if (marker.getElement && typeof marker.getElement === 'function') {
      const element = marker.getElement();
      if (element && element.classList) {
        element.classList.remove('overlapping-marker-pulse');
        return true;
      }
    }
    return false;
  };

  // Helper: Calculate opacity for pulse animation
  const _calculatePulseOpacity = (cycleDuration) => {
    const elapsed = Date.now() % cycleDuration;
    const progress = elapsed / cycleDuration;
    if (progress < 0.5) {
      return 1 - (progress * 2) * 0.7;
    }
    return 0.3 + ((progress - 0.5) * 2) * 0.7;
  };

  // Helper: Start opacity animation for standard markers
  const _startOpacityAnimation = (marker, conditionFn) => {
    if (marker._originalOpacity === undefined) {
      marker._originalOpacity = marker.getOpacity() || 1;
    }
    if (!marker._pulseInterval) {
      const cycleDuration = 700;
      marker._pulseInterval = setInterval(() => {
        if (conditionFn()) {
          const opacity = _calculatePulseOpacity(cycleDuration);
          marker.setOpacity(opacity);
        }
      }, 50);
    }
  };

  // Helper: Stop opacity animation and restore original
  const _stopOpacityAnimation = (marker) => {
    if (marker._pulseInterval) {
      clearInterval(marker._pulseInterval);
      marker._pulseInterval = null;
    }
    if (marker._originalOpacity !== undefined) {
      marker.setOpacity(marker._originalOpacity);
    }
  };

  // Helper: Handle pulse for spiderfied markers
  const _handleSpiderfiedMarkerPulse = (marker, shouldPulse) => {
    if (shouldPulse) {
      if (!_addPulseClass(marker)) {
        _startOpacityAnimation(marker, () => marker._isHoveredSpiderfied && marker._isSpiderfied);
      }
    } else {
      if (!_removePulseClass(marker)) {
        _stopOpacityAnimation(marker);
      }
    }
  };

  // Helper: Handle pulse for overlapping markers
  const _handleOverlappingMarkerPulse = (marker, omsInstance) => {
    let nearbyMarkers;
    try {
      nearbyMarkers = omsInstance.markersNearMarker(marker);
    } catch (error) {
      console.warn('Map not ready for overlap detection:', error);
      return;
    }
    const hasOverlap = nearbyMarkers && nearbyMarkers.length > 0;
    marker._hasOverlap = hasOverlap;
    const shouldPulse = hasOverlap && marker._isHoveredCluster;

    if (shouldPulse) {
      if (!_addPulseClass(marker)) {
        _startOpacityAnimation(marker, () => marker._hasOverlap && marker._isHoveredCluster && !marker._isSpiderfied);
      }
    } else {
      if (!_removePulseClass(marker)) {
        _stopOpacityAnimation(marker);
      }
    }
  };

  const updateOverlappingMarkersPulse = useCallback((omsInstance) => {
    if (!omsInstance || !mapIsIdle.current) return;
    allTrackedMarkers.current.forEach((marker) => {
      if (marker._isSpiderfied) {
        const shouldPulse = marker._isHoveredSpiderfied === true;
        _handleSpiderfiedMarkerPulse(marker, shouldPulse);
        return;
      }
      _handleOverlappingMarkerPulse(marker, omsInstance);
    });
  }, []);

  // Prevent browser's default context menu - use global handler
  React.useEffect(() => {
    setupGlobalContextMenuPrevention();
    return () => {
      teardownGlobalContextMenuPrevention();
    };
  }, []);

  // Initialize OverlappingMarkerSpiderfier when map loads
  const onMapLoad = useCallback((map) => {
    // Store reference to map div for context menu prevention
    mapDivRef.current = map.getDiv();
    
    // Prevent context menu on the map div using Google Maps event system
    const mapDiv = map.getDiv();
    
    if (mapDiv && window.google && window.google.maps) {
      window.google.maps.event.addDomListener(mapDiv, 'contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.stopImmediatePropagation) e.stopImmediatePropagation();
        return false;
      }, true); // true = capture phase
    }
    
    // Also add to all child elements and watch for new ones
    if (mapDiv) {
      const preventContextMenu = (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.stopImmediatePropagation) e.stopImmediatePropagation();
        return false;
      };
      mapDiv.addEventListener('contextmenu', preventContextMenu, { capture: true });
      
      // Add listener to all existing children
      const addListenerToElement = (el) => {
        el.addEventListener('contextmenu', preventContextMenu, { capture: true });
      };
      
      const allChildren = mapDiv.querySelectorAll('*');
      allChildren.forEach(addListenerToElement);
      
      // Helper: Process a single added node
      const processAddedNode = (node) => {
        if (node.nodeType !== 1) return; // Not an element node
        addListenerToElement(node);
        // Also add to all descendants
        const descendants = node.querySelectorAll ? node.querySelectorAll('*') : [];
        descendants.forEach(addListenerToElement);
      };
      
      // Helper: Process all added nodes from mutations
      const processMutations = (mutations) => {
        mutations.forEach((mutation) => {
          mutation.addedNodes.forEach(processAddedNode);
        });
      };
      
      // Use MutationObserver to catch dynamically added elements (like markers)
      const observer = new MutationObserver(processMutations);
      
      observer.observe(mapDiv, {
        childList: true,
        subtree: true
      });
      
      // Store observer for potential cleanup
      mapDiv._contextMenuObserver = observer;
    }

    if (parentOnLoad) {
      parentOnLoad(map);
    }
    function _stopMarkerPulse(marker) {
      if (marker.content && marker.content.classList) {
        marker.content.classList.remove('overlapping-marker-pulse');
      } else if (marker.getElement && typeof marker.getElement === 'function') {
        const element = marker.getElement();
        if (element && element.classList) {
          element.classList.remove('overlapping-marker-pulse');
        }
      } else {
        if (marker._pulseInterval) {
          clearInterval(marker._pulseInterval);
          marker._pulseInterval = null;
        }
        if (marker._originalOpacity !== undefined) {
          marker.setOpacity(marker._originalOpacity);
        }
      }
    }

    if (enableSpiderfier) {
      const omsInstance = new OverlappingMarkerSpiderfier(
        map,
        { ...defaultSpiderfierOptions, ...spiderfierOptions }
      );
      // Set leg colors for better visibility
      const mapTypes = window.google.maps.MapTypeId;
      omsInstance.legColors.usual[mapTypes.ROADMAP] = '#444';
      omsInstance.legColors.usual[mapTypes.SATELLITE] = '#fff';
      omsInstance.legColors.usual[mapTypes.HYBRID] = '#fff';
      omsInstance.legColors.usual[mapTypes.TERRAIN] = '#444';
      omsInstance.legColors.highlighted[mapTypes.ROADMAP] = '#f00';
      omsInstance.legColors.highlighted[mapTypes.SATELLITE] = '#f00';
      omsInstance.legColors.highlighted[mapTypes.HYBRID] = '#f00';
      omsInstance.legColors.highlighted[mapTypes.TERRAIN] = '#f00';

      omsInstance.addListener('click', function(marker, event) {
        // Check if shift key is held for alternative action (clustering view)
        const isShiftClick = event && event.domEvent && event.domEvent.shiftKey;
        
        if (isShiftClick && marker._onMarkerRightClick) {
          // Shift+Click triggers the alternative action (previously right-click)
          marker._onMarkerRightClick(event);
        } else if (marker._onMarkerLeftClick) {
          // Normal click triggers the primary action
          marker._onMarkerLeftClick(event);
        }
      });
      omsInstance.addListener('spiderfy', function(markers) {
        // Mark all spiderfied markers and protect their spider positions
        markers.forEach(function(marker) {
          marker._isSpiderfied = true;
          marker._spiderPositionProtected = true;
          marker._pendingPosition = null;
          marker._isHoveredCluster = false; // Stop pulsing when spiderfied
          marker.setZIndex(google.maps.Marker.MAX_ZINDEX + 100);
          // Set tooltip to show collapse instruction + original title
          if (marker._originalTitle === undefined) {
            marker._originalTitle = marker.getTitle() || '';
          }
          const collapseHint = 'Click on the map to collapse back';
          const newTitle = marker._originalTitle 
            ? `${collapseHint}\n───────────────────\n${marker._originalTitle}` 
            : collapseHint;
          marker.setTitle(newTitle);
          _stopMarkerPulse(marker);
        });
      });
      omsInstance.addListener('unspiderfy', function(markers) {
        markers.forEach(function(marker) {
          marker._spiderPositionProtected = false;
          marker._isSpiderfied = false;
          marker._isHoveredSpiderfied = false;
          marker._isHoveredCluster = false;
          marker.setZIndex(undefined);
          if (marker._originalTitle !== undefined) {
            marker.setTitle(marker._originalTitle);
          }
          if (marker._pendingPosition) {
            if (marker._origSetPosition) {
              marker._origSetPosition(marker._pendingPosition);
            }
            marker._pendingPosition = null;
          }
          _stopMarkerPulse(marker);
        });
      });
      // Add any pending markers
      if (pendingMarkers.current.length > 0) {
        pendingMarkers.current.forEach((marker) => {
          try {
            omsInstance.trackMarker(marker);
            _wrapMarkerPositionMethods(marker);
            // Store OMS instance reference in marker for later access
            marker._omsInstance = omsInstance;
            // Add to tracked markers list
            if (!allTrackedMarkers.current.includes(marker)) {
              allTrackedMarkers.current.push(marker);
            }
          } catch (error) {
            console.error('Error adding pending marker:', error);
          }
        });
        pendingMarkers.current = [];
        // No auto-pulse - only pulse on hover
      }
      // Clean up existing listeners
      mapListeners.current.forEach(listener => {
        window.google.maps.event.removeListener(listener);
      });
      mapListeners.current = [];
      // Add listener for map idle event - required by OMS before calling markersNearMarker
      const idleListener = map.addListener('idle', () => {
        mapIsIdle.current = true;
        // No auto-pulse - only pulse on hover
      });
      mapListeners.current.push(idleListener);
      // Helper function to clear all hover states and stop animations
      const clearAllHoverStates = () => {
        allTrackedMarkers.current.forEach(marker => {
          marker._isHoveredCluster = false;
          marker._isHoveredSpiderfied = false;
          // Restore original title
          if (marker._originalTitle !== undefined) {
            marker.setTitle(marker._originalTitle);
          }
          // Stop pulse animation
          if (marker._pulseInterval) {
            clearInterval(marker._pulseInterval);
            marker._pulseInterval = null;
          }
          // Restore original opacity
          if (marker._originalOpacity !== undefined) {
            marker.setOpacity(marker._originalOpacity);
          }
          // Remove CSS class if any
          if (marker.content && marker.content.classList) {
            marker.content.classList.remove('overlapping-marker-pulse');
          } else if (marker.getElement && typeof marker.getElement === 'function') {
            const element = marker.getElement();
            if (element && element.classList) {
              element.classList.remove('overlapping-marker-pulse');
            }
          }
        });
      };
      // Add listener for zoom changes to mark map as not idle and clear hover states
      const zoomListener = map.addListener('zoom_changed', () => {
        mapIsIdle.current = false;
        clearAllHoverStates();
      });
      mapListeners.current.push(zoomListener);
      // Add listener for map drag start to mark as not idle and clear hover states
      const dragStartListener = map.addListener('dragstart', () => {
        mapIsIdle.current = false;
        clearAllHoverStates();
      });
      mapListeners.current.push(dragStartListener);
      setOms(omsInstance);
    }
  }, [enableSpiderfier, spiderfierOptions, parentOnLoad, updateOverlappingMarkersPulse]);

  React.useEffect(() => {
    return () => {
      // Cleanup all map listeners
      mapListeners.current.forEach(listener => {
        if (window.google && window.google.maps && window.google.maps.event) {
          window.google.maps.event.removeListener(listener);
        }
      });
      mapListeners.current = [];
      // Cleanup all marker pulse intervals
      allTrackedMarkers.current.forEach(marker => {
        if (marker._pulseInterval) {
          clearInterval(marker._pulseInterval);
          marker._pulseInterval = null;
        }
      });
    };
  }, []);

  const renderMap = () => {
    mapContainerStyle = mapContainerStyle || { height: "100%" };
    options = _.merge(
      {
        styles: DefaultMapStyle,
        streetViewControl: false,
        mapTypeControl: false,
        fullscreenControl: false,
        zoomControlOptions: {
          position:
            window.google && window.google.maps.ControlPosition.RIGHT_CENTER,
        },
      },
      options
    );

    // Helper: Prevent context menu on marker element
    const _preventContextMenuOnMarker = (marker) => {
      try {
        if (marker.getElement && typeof marker.getElement === 'function') {
          const element = marker.getElement();
          if (element) {
            element.addEventListener('contextmenu', (e) => {
              e.preventDefault();
              e.stopPropagation();
            }, { capture: true });
          }
        }
        if (marker.content) {
          marker.content.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            e.stopPropagation();
          }, { capture: true });
        }
      } catch (err) {
        // Silently ignore if we can't access the marker element
      }
    };

    // Helper: Prevent default event behavior
    const _preventEventDefault = (event) => {
      if (event && event.domEvent) {
        event.domEvent.preventDefault();
        event.domEvent.stopPropagation();
        if (event.domEvent.stopImmediatePropagation) {
          event.domEvent.stopImmediatePropagation();
        }
      }
      if (event && event.stop) {
        event.stop();
      }
    };

    // Helper: Reset triggering ref after delay
    const _resetTriggeringRef = (isTriggeringRef) => {
      isTriggeringRef.current = false;
    };

    // Helper: Schedule delayed reset of triggering ref
    const _scheduleDelayedReset = (isTriggeringRef) => {
      setTimeout(() => _resetTriggeringRef(isTriggeringRef), 500);
    };

    // Helper: Schedule reset of triggering ref with double delay
    const _scheduleTriggeringRefReset = (isTriggeringRef) => {
      setTimeout(() => _scheduleDelayedReset(isTriggeringRef), 0);
    };

    // Helper: Handle right click when markers are nearby
    const _handleRightClickWithNearbyMarkers = (marker, event, isTriggeringRef) => {
      const isSpiderfied = marker._isSpiderfied === true;
      if (!isSpiderfied) {
        isTriggeringRef.current = true;
        _scheduleTriggeringRefReset(isTriggeringRef);
        return;
      }
      if (marker._onMarkerRightClick) {
        marker._onMarkerRightClick(event);
      }
    };

    // Helper: Process right click event
    const _processRightClickEvent = (marker, event, isTriggeringRef) => {
      if (isTriggeringRef.current) return;
      
      const omsInstance = marker._omsInstance;
      if (!omsInstance) {
        if (marker._onMarkerRightClick) {
          marker._onMarkerRightClick(event);
        }
        return;
      }
      
      const nearbyMarkers = omsInstance.markersNearMarker(marker);
      if (nearbyMarkers && nearbyMarkers.length > 0) {
        _handleRightClickWithNearbyMarkers(marker, event, isTriggeringRef);
      } else if (marker._onMarkerRightClick) {
        marker._onMarkerRightClick(event);
      }
    };

    // Helper: Handle right click on marker
    const _handleMarkerRightClick = (marker) => {
      const isTriggeringRef = { current: false };
      marker.addListener('rightclick', function(event) {
        _preventEventDefault(event);
        _processRightClickEvent(marker, event, isTriggeringRef);
      });
    };

    // Helper: Mark spiderfied marker as hovered
    const _markSpiderfiedMarkerAsHovered = (m) => {
      if (m._isSpiderfied) {
        m._isHoveredSpiderfied = true;
      }
    };

    // Helper: Mark nearby marker as hovered cluster
    const _markNearbyMarkerAsHoveredCluster = (nearbyMarker) => {
      nearbyMarker._isHoveredCluster = true;
      if (nearbyMarker._originalTitle === undefined) {
        nearbyMarker._originalTitle = nearbyMarker.getTitle() || '';
      }
    };

    // Helper: Handle mouseover on marker
    const _handleMarkerMouseover = (marker) => {
      marker.addListener('mouseover', function() {
        const omsInstance = marker._omsInstance;
        if (!omsInstance || !mapIsIdle.current) return;
        
        if (marker._isSpiderfied) {
          allTrackedMarkers.current.forEach(_markSpiderfiedMarkerAsHovered);
          updateOverlappingMarkersPulse(omsInstance);
          return;
        }
        
        try {
          const nearbyMarkers = omsInstance.markersNearMarker(marker);
          if (nearbyMarkers && nearbyMarkers.length > 0) {
            marker._isHoveredCluster = true;
            if (marker._originalTitle === undefined) {
              marker._originalTitle = marker.getTitle() || '';
            }
            marker.setTitle('Left Click to spread out icons too close');
            
            nearbyMarkers.forEach(_markNearbyMarkerAsHoveredCluster);
            updateOverlappingMarkersPulse(omsInstance);
          }
        } catch (error) {
          // Ignore errors if map not ready
        }
      });
    };

    function _clearMarkerHoverState(marker) {
      marker._isHoveredCluster = false;
      if (marker._originalTitle !== undefined) {
        marker.setTitle(marker._originalTitle);
      }
    }

    function _clearSpiderfiedHoverFlags() {
      allTrackedMarkers.current.forEach(m => {
        if (m._isSpiderfied) {
          m._isHoveredSpiderfied = false;
        }
      });
    }

    // Helper: Handle mouseout on marker
    const _handleMarkerMouseout = (marker) => {
      marker.addListener('mouseout', function() {
        const omsInstance = marker._omsInstance;
        if (!omsInstance || !mapIsIdle.current) return;
        
        if (marker._isSpiderfied) {
          _clearSpiderfiedHoverFlags();
          updateOverlappingMarkersPulse(omsInstance);
          return;
        }
        
        try {
          const nearbyMarkers = omsInstance.markersNearMarker(marker);
          _clearMarkerHoverState(marker);
          if (nearbyMarkers) {
            nearbyMarkers.forEach(_clearMarkerHoverState);
          }
          updateOverlappingMarkersPulse(omsInstance);
        } catch (error) {
          // Ignore errors if map not ready
        }
      });
    };

    // Helper: Add marker to spiderfier
    const _addMarkerToSpiderfier = (marker) => {
      if (oms) {
        try {
          oms.trackMarker(marker);
          _wrapMarkerPositionMethods(marker);
          marker._omsInstance = oms;
          if (!allTrackedMarkers.current.includes(marker)) {
            allTrackedMarkers.current.push(marker);
          }
        } catch (error) {
          console.error(error);
        }
      } else {
        pendingMarkers.current.push(marker);
      }
    };

    // Inject onLoad to children markers if spiderfier is enabled
    const enhancedChildren = enableSpiderfier
      ? React.Children.map(props.children, child => {
          if (!child || !React.isValidElement(child)) return child;
          // Inject onLoad to add markers to spiderfier
          return React.cloneElement(child, {
            enableSpiderfier: enableSpiderfier,
            onLoad: (marker, markerClick) => {
              if (!markerClick) return;
              
              if (child.props.onLoad) {
                child.props.onLoad(marker, markerClick);
              }
              
              _preventContextMenuOnMarker(marker);
              
              const { leftClick, rightClick } = markerClick;
              if (leftClick) {
                marker._onMarkerLeftClick = leftClick;
              }
              if (rightClick) {
                marker._onMarkerRightClick = rightClick;
                _handleMarkerRightClick(marker);
              }
              
              _handleMarkerMouseover(marker);
              _handleMarkerMouseout(marker);
              _addMarkerToSpiderfier(marker);
            }
          });
        })
      : props.children;

    return (
      <div 
        ref={containerRef} 
        style={{ height: '100%', width: '100%' }}
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
          return false;
        }}
      >
        <GoogleMap
          mapContainerStyle={mapContainerStyle}
          options={options}
          onLoad={onMapLoad}
          {...otherProps}
        >
          {enhancedChildren}
        </GoogleMap>
      </div>
    );
  };

  if (loadError) {
    const msg = new UserMessage(
      UserMessage.SRC_TYPE.GENERIC,
      UserMessage.MESSAGE_LEVEL.ERROR,
      usrMsgPurpose,
      "Failure. " + loadError.message
    );

    return (
      <LoaderDisplay type={LoaderDisplay.TYPE.LOAD_FAILURE} userMessage={msg} />
    );
  }

  const loadingUsrMsg = new UserMessage(
    UserMessage.SRC_TYPE.GENERIC,
    UserMessage.MESSAGE_LEVEL.INFO,
    usrMsgPurpose,
    DefaultLoadingUserMessage
  );

  return isLoaded ? (
    renderMap()
  ) : (
    <LoaderDisplay
      type={LoaderDisplay.TYPE.LOADING}
      userMessage={loadingUsrMsg}
    />
  );
}

Map.propTypes = {
  /**
   * @see {@link https://developers.google.com/maps/documentation/javascript/get-api-key}
   */
  apiKey: PropTypes.string,
  /**
   * @see {@link https://developers.google.com/maps/documentation/javascript/libraries}
   * Default: ['drawing']
   */
  libraries: PropTypes.arrayOf(PropTypes.string),
  /**
   * @see {@link https://developers.google.com/maps/documentation/javascript/versions}
   * Default: 'quarterly' -> most recent stable version
   */
  version: PropTypes.string,
  /** Name of this instance for self recognition. Default is empty string. */
  name: PropTypes.string,
  /**
   * @see {@link https://react-google-maps-api-docs.netlify.app/#googlemap}
   * Default: { height: '100%' }
   */
  mapContainerStyle: PropTypes.object,
  /**
   * @see {@link https://react-google-maps-api-docs.netlify.app/#googlemap}
   * Default:
   *   options: {
   *     styles: DefaultMapStyle,
   *     streetViewControl: false,
   *     mapTypeControl: false,
   *     fullscreenControl: false,
   *     zoomControlOptions: {
   *       position: 8  // google.maps.ControlPosition.RIGHT_CENTER
   *     },
   *   },
   */
  options: PropTypes.object,
  /**
   * Enable OverlappingMarkerSpiderfier for handling overlapping markers
   * Default: false
   */
  enableSpiderfier: PropTypes.bool,
  /**
   * Custom options for OverlappingMarkerSpiderfier
   * @see {@link https://www.npmjs.com/package/ts-overlapping-marker-spiderfier}
   * Available options:
   *   - markersWontMove (default: true)
   *   - markersWontHide (default: true)
   *   - basicFormatEvents (default: true)
   *   - keepSpiderfied (default: false)
   *   - nearbyDistance (default: 20)
   *   - circleSpiralSwitchover (default: 9)
   *   - legWeight (default: 1.5)
   */
  spiderfierOptions: PropTypes.object,
};
