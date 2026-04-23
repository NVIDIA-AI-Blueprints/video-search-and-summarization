export const formatTimestamp = (timestamp: string): string => {
  try {
    const date = new Date(timestamp);
    const dateStr = date.toLocaleDateString('en-US', {
      month: '2-digit',
      day: '2-digit',
      year: 'numeric',
    });
    const timeStr = date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    });
    return `${dateStr} ${timeStr}`;
  } catch {
    return timestamp;
  }
};
