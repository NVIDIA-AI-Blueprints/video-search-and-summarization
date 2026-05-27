/**********
This library is free software; you can redistribute it and/or modify it under
the terms of the GNU Lesser General Public License as published by the
Free Software Foundation; either version 3 of the License, or (at your
option) any later version. (See <http://www.gnu.org/copyleft/lesser.html>.)

This library is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for
more details.

You should have received a copy of the GNU Lesser General Public License
along with this library; if not, write to the Free Software Foundation, Inc.,
51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
**********/
// "Live555Config"
// Global static configuration class for Live555
// C++ header

#ifndef _LIVE555CONFIG_HH
#define _LIVE555CONFIG_HH

#ifndef _BOOLEAN_HH
#include "Boolean.hh"
#endif

#include <string>
#include <map>
#include <mutex>

class Live555Config {
public:
  // Get the singleton instance
  static Live555Config& instance();

  // String configuration getters/setters
  void setString(const char* key, const char* value);
  char const* getString(const char* key, const char* defaultValue = NULL);
  
  // Integer configuration getters/setters
  void setInt(const char* key, int value);
  int getInt(const char* key, int defaultValue = 0);
  
  // Boolean configuration getters/setters
  void setBoolean(const char* key, Boolean value);
  Boolean getBoolean(const char* key, Boolean defaultValue = False);
  
  // Float configuration getters/setters
  void setFloat(const char* key, float value);
  float getFloat(const char* key, float defaultValue = 0.0f);
  
  // Double configuration getters/setters
  void setDouble(const char* key, double value);
  double getDouble(const char* key, double defaultValue = 0.0);
  
  // Configuration management
  void clear(); // Clear all configuration values
  Boolean hasKey(const char* key); // Check if key exists
  void removeKey(const char* key); // Remove a specific key
  
  // Load/Save configuration from/to file
  Boolean loadFromFile(const char* filename);
  Boolean saveToFile(const char* filename);
  
  // Debug/utility functions
  void printAllConfig(); // Print all configuration values
  unsigned getConfigCount(); // Get total number of config entries

private:
  Live555Config(); // Private constructor for singleton
  ~Live555Config();
  Live555Config(const Live555Config&); // Prevent copying
  Live555Config& operator=(const Live555Config&); // Prevent assignment
  
  // Internal storage
  std::map<std::string, std::string> fStringConfigs;
  std::map<std::string, int> fIntConfigs;
  std::map<std::string, Boolean> fBooleanConfigs;
  std::map<std::string, float> fFloatConfigs;
  std::map<std::string, double> fDoubleConfigs;
  
  // Thread safety
  std::mutex fMutex;
  
  // Utility functions
  std::string trim(const std::string& str);
  Boolean parseBooleanValue(const char* value);
};

// Convenience macros for common usage patterns
#define CONFIG Live555Config::instance()
#define CONFIG_GET_STRING(key, def) Live555Config::instance().getString(key, def)
#define CONFIG_SET_STRING(key, val) Live555Config::instance().setString(key, val)
#define CONFIG_GET_INT(key, def) Live555Config::instance().getInt(key, def)
#define CONFIG_SET_INT(key, val) Live555Config::instance().setInt(key, val)
#define CONFIG_GET_BOOL(key, def) Live555Config::instance().getBoolean(key, def)
#define CONFIG_SET_BOOL(key, val) Live555Config::instance().setBoolean(key, val)
#define CONFIG_GET_FLOAT(key, def) Live555Config::instance().getFloat(key, def)
#define CONFIG_SET_FLOAT(key, val) Live555Config::instance().setFloat(key, val)
#define CONFIG_GET_DOUBLE(key, def) Live555Config::instance().getDouble(key, def)
#define CONFIG_SET_DOUBLE(key, val) Live555Config::instance().setDouble(key, val)

#endif 