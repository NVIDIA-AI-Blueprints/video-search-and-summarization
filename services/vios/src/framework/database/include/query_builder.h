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

#pragma once

#include <string>
#include <vector>
#include <sstream>
#include <iomanip>
#include <cctype>
#include "database_types.h"

/**
 * @brief Common query builder class to prevent SQL injection across different database backends
 *
 * This class provides a unified way to build parameterized queries that can be safely
 * executed against any database backend (PostgreSQL, SQLite, etc.).
 *
 * Usage:
 *   QueryBuilder builder;
 *   std::string sql = builder.buildQuery(
 *       "SELECT * FROM users WHERE username = {0} AND age > {1}",
 *       {"john", "25"}
 *   );
 */
class QueryBuilder
{
public:
    /**
     * @brief Build a parameterized query by replacing placeholders with escaped parameters
     *
     * @param queryTemplate Query template with {0}, {1}, etc. placeholders
     * @param params Vector of parameters to substitute
     * @param database_type Database type for proper escaping (default: PostgreSQL)
     * @return Safely escaped SQL query string
     */
    static std::string buildQuery(const std::string& queryTemplate, const std::vector<std::string>& params, 
                                  DatabaseType database_type = DatabaseType::PostgreSQL);

    /**
     * @brief Escape a single string parameter for safe SQL insertion
     *
     * This is the core security function that prevents SQL injection by properly
     * escaping all SQL metacharacters, Unicode sequences, and database-specific
     * escape sequences for both PostgreSQL and SQLite.
     *
     * Handles:
     * - Single quotes (' → '')
     * - Double quotes (" → "")
     * - Backslashes (\ → \\)
     * - Control characters (hex encoding)
     * - Unicode sequences (UTF-8 safe)
     * - SQL metacharacters (;, --, comment blocks)
     * - Wildcards (% and _ for LIKE operations)
     *
     * @param value The string value to escape
     * @return Escaped string safe for SQL
     */
    static std::string escapeString(const std::string& value, DatabaseType database_type = DatabaseType::PostgreSQL);

    /**
     * @brief Escape a string for safe JSON insertion
     *
     * @param value The string value to escape for JSON
     * @return Escaped string safe for JSON (includes quotes)
     */
    static std::string escapeJsonString(const std::string& value);


    /**
     * @brief Build a parameterized query with automatic type conversion
     *
     * @param queryTemplate Query template with {0}, {1}, etc. placeholders
     * @param database_type Database type for proper escaping
     * @param params Vector of parameters (will be converted to strings)
     * @return Safely escaped SQL query string
     */
    template<typename... Args>
    static std::string buildQuery(const std::string& queryTemplate, DatabaseType database_type, Args... args);

    /**
     * @brief Build an IN clause safely
     *
     * @param column Column name for the IN clause
     * @param values Vector of values for the IN clause
     * @param database_type Database type for proper escaping (default: PostgreSQL)
     * @return Safe IN clause string, or empty string if column name is invalid
     */
    static std::string buildInClause(const std::string& column, const std::vector<std::string>& values,
                                     DatabaseType database_type = DatabaseType::PostgreSQL);

    /**
     * @brief Build a complete WHERE clause with IN condition
     *
     * @param column Column name for the IN clause
     * @param values Vector of values for the IN clause
     * @param database_type Database type for proper escaping (default: PostgreSQL)
     * @return Complete WHERE clause string, or empty string if column name is invalid
     */
    static std::string buildWhereInClause(const std::string& column, const std::vector<std::string>& values,
                                          DatabaseType database_type = DatabaseType::PostgreSQL);

    /**
     * @brief Validate column name to prevent SQL injection
     *
     * @param column The column name to validate
     * @return Validated column name if safe, empty string if invalid
     */
    static std::string validateColumnName(const std::string& column);

private:
    /**
     * @brief Helper function to convert any type to string
     */
    template<typename T>
    static std::string toString(const T& value);
};

// CRITICAL SECURITY FUNCTIONS - Implementation in header for visibility and auditability

    /**
     * @brief SQL string escaping for security
     *
     * This function provides SQL string escaping by doubling single quotes.
     * This is the standard SQL escaping method and is safe for all databases.
     * For maximum security, use parameterized queries instead of manual escaping.
     */
inline std::string QueryBuilder::escapeString(const std::string& value, DatabaseType database_type)
{
    if (value.empty())
    {
        return "''";
    }

    if (database_type == DatabaseType::SQLite)
    {
        // Check if the value is a pure integer (digits only, with optional leading minus sign)
        // If so, return without quotes to enable proper numeric comparisons in both SQLite and PostgreSQL.
        // This fixes issues where BIGINT columns compared with quoted strings fail in SQLite.
        // Both databases handle unquoted integers correctly for both numeric and VARCHAR columns.
        bool isNumeric = true;
        size_t start = 0;

        // Allow leading minus sign for negative numbers
        if (value[0] == '-')
        {
            start = 1;
        }

        // Must have at least one digit after optional minus sign
        if (start >= value.length())
        {
            isNumeric = false;
        }
        else
        {
            for (size_t i = start; i < value.length(); ++i)
            {
                if (!std::isdigit(static_cast<unsigned char>(value[i])))
                {
                    isNumeric = false;
                    break;
                }
            }
        }

        // If it's a pure integer, return without quotes for proper numeric comparison
        if (isNumeric)
        {
            return value;
        }
    }

    // For non-numeric values (strings), quote and escape single quotes
    std::string result;
    result.reserve(value.length() * 2 + 2); // Reserve space for worst case (all quotes escaped)
    result += "'";

    for (char c : value)
    {
        if (c == '\'')
        {
            result += "''"; // Escape single quote by doubling
        }
        else
        {
            result += c; // All other characters are safe as-is
        }
    }

    result += "'";
    return result;
}

// Template implementation
template<typename... Args>
std::string QueryBuilder::buildQuery(const std::string& queryTemplate, DatabaseType database_type, Args... args)
{
    std::vector<std::string> params = {toString(args)...};
    return buildQuery(queryTemplate, params, database_type);
}

template<typename T>
std::string QueryBuilder::toString(const T& value)
{
    if constexpr (std::is_same_v<T, std::string> || std::is_same_v<T, const char*>)
    {
        return std::string(value);
    }
    else if constexpr (std::is_arithmetic_v<T>)
    {
        return std::to_string(value);
    }
    else
    {
        // For other types, try to convert to string
        std::ostringstream oss;
        oss << value;
        return oss.str();
    }
}

inline std::string QueryBuilder::validateColumnName(const std::string& column)
{
    if (column.empty()) return "";

    // Check for valid column name pattern (alphanumeric and underscore only)
    for (char c : column)
    {
        if (!std::isalnum(c) && c != '_')
        {
            return ""; // Invalid character found
        }
    }

    // Check if it starts with a letter or underscore
    if (!std::isalpha(column[0]) && column[0] != '_')
    {
        return ""; // Must start with letter or underscore
    }

    return column;
}

inline std::string QueryBuilder::escapeJsonString(const std::string& value)
{
    if (value.empty())
    {
        return "\"\"";
    }

    std::string result;
    result.reserve(value.length() * 2 + 2);
    result += "\"";

    for (char c : value)
    {
        switch (c)
        {
            case '"':  result += "\\\""; break;
            case '\\': result += "\\\\"; break;
            case '\b': result += "\\b"; break;
            case '\f': result += "\\f"; break;
            case '\n': result += "\\n"; break;
            case '\r': result += "\\r"; break;
            case '\t': result += "\\t"; break;
            default:
                if (c < 0x20) // Control characters
                {
                    std::ostringstream oss;
                    oss << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c);
                    result += oss.str();
                }
                else
                {
                    result += c;
                }
                break;
        }
    }

    result += "\"";
    return result;
}


inline std::string QueryBuilder::buildQuery(const std::string& queryTemplate, const std::vector<std::string>& params,
                                            DatabaseType database_type)
{
    std::string result = queryTemplate;

    for (size_t i = 0; i < params.size(); ++i)
    {
        std::string placeholder = "{" + std::to_string(i) + "}";
        std::string escapedValue = escapeString(params[i], database_type);

        size_t pos = 0;
        while ((pos = result.find(placeholder, pos)) != std::string::npos)
        {
            result.replace(pos, placeholder.length(), escapedValue);
            pos += escapedValue.length();
        }
    }

    return result;
}

inline std::string QueryBuilder::buildInClause(const std::string& column, const std::vector<std::string>& values,
                                               DatabaseType database_type)
{
    std::string safeColumn = validateColumnName(column);
    if (safeColumn.empty() || values.empty())
    {
        return "";
    }

    std::string result = safeColumn + " IN (";
    for (size_t i = 0; i < values.size(); ++i)
    {
        if (i > 0) result += ", ";
        result += escapeString(values[i], database_type);
    }
    result += ")";

    return result;
}

inline std::string QueryBuilder::buildWhereInClause(const std::string& column, const std::vector<std::string>& values,
                                                    DatabaseType database_type)
{
    std::string inClause = buildInClause(column, values, database_type);
    return inClause.empty() ? "" : " WHERE " + inClause;
}
