// SPDX-License-Identifier: MIT
const formatDatetime = (date: Date): string => {
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  
    const m = months[date.getMonth()];
    const d = date.getDate();
    const y = date.getFullYear();
  
    const hh = String(date.getHours()).padStart(2, "0");
    const mm = String(date.getMinutes()).padStart(2, "0");
    const ss = String(date.getSeconds()).padStart(2, "0");
  
    return `${m} ${d}, ${y} @ ${hh}:${mm}:${ss}`;
  }

/**
 * Parse date string as local time without timezone conversion
 * Prevents automatic timezone adjustment when displaying time from API
 */
const parseDateAsLocal = (dateString: string): Date | null => {
    // Guard against missing/invalid timestamps
    if (!dateString || typeof dateString !== 'string' || !dateString.trim()) {
        return null;
    }
    
    // Remove timezone info (Z or +00:00) if present to prevent UTC conversion
    const cleanedDateString = dateString.replace(/Z$/, '').replace(/[+-]\d{2}:\d{2}$/, '');
    
    // Parse as local time
    const date = new Date(cleanedDateString);
    
    // Check if date is valid
    if (isNaN(date.getTime())) {
        return null;
    }
    
    return date;
}

const formatTime = (date: Date | null): string => {
    // Guard against null or invalid date
    if (!date || isNaN(date.getTime())) {
        return '--:--:--';  // Placeholder for missing/invalid time
    }
    
    const hours = String(date.getHours()).padStart(2, "0");
    const minutes = String(date.getMinutes()).padStart(2, "0");
    const seconds = String(date.getSeconds()).padStart(2, "0");
    return `${hours}:${minutes}:${seconds}`;
}

/**
 * Format Date to ISO string but preserve local timezone
 * Prevents automatic UTC conversion when sending to API
 */
const formatDateToLocalISO = (date: Date | null): string | null => {
    if (!date) return null;
    
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    const hours = String(date.getHours()).padStart(2, "0");
    const minutes = String(date.getMinutes()).padStart(2, "0");
    const seconds = String(date.getSeconds()).padStart(2, "0");
    
    return `${year}-${month}-${day}T${hours}:${minutes}:${seconds}`;
}

export { formatDatetime, formatTime, formatDateToLocalISO, parseDateAsLocal };