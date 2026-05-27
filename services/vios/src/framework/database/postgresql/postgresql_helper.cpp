/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "postgresql_helper.h"
#include "streamrecorder.h"
#include "vst_common.h"
#include "utils.h"
#include "database_common.h"
#include "gst_utils.h"
#include "storage_management.h"
#include "modules_apis.h"
#include "query_builder.h"
#include <chrono>
#include <optional>
#include <stdexcept>

using namespace nv_vms;

static void sensorDetailsHelper(SensorDetailsDBColumns &row, unordered_map<string, string> &entries)
{
    for (auto column : entries)
    {
        if (iequals(column.first, SensorDetailsDBColumns::device_id))
        {
            row.device_id_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::sensor_id))
        {
            row.sensor_id_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::sensor_hw_id))
        {
            row.sensor_hw_id_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::username))
        {
            row.username_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::password))
        {
            std::string encryptedPassword = column.second;
            std::string decryptedPassword = EMPTY_STRING;
            if (!encryptedPassword.empty())
            {
                if (!vst_common::decrypt_data(encryptedPassword, decryptedPassword, row.sensor_id_value))
                {
                    LOG(error) << "Failed to decrypt password for sensor: " << row.sensor_id_value 
                               << ", encrypted password length: " << encryptedPassword.length() 
                               << ", IV: " << row.sensor_id_value 
                               << ". Password may have been encrypted with different key. Please re-enter credentials." << endl;
                    // Leave decryptedPassword empty to indicate credentials need to be re-entered
                }
            }
            row.password_value = decryptedPassword;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::name))
        {
            row.name_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::ip))
        {
            row.ip_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::hardware))
        {
            row.hardware_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::manufacturer))
        {
            row.manufacturer_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::serial_number))
        {
            row.serial_number_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::firmware_version))
        {
            row.firmware_version_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::hardware_id))
        {
            row.hardware_id_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::location))
        {
            row.location_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::tags))
        {
            row.tags_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::url))
        {
            row.url_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::type))
        {
            row.type_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::position))
        {
            row.position_value = column.second;
            /**
             *  NOTE: Not required for postgresql
             *   if (row.position_value.size() >= 2)
             *   {
             *       // remove quotation from JSON string
             *       row.position_value = row.position_value.substr(1, row.position_value.size() - 2);
             *   }
             */
        }
        else if (iequals(column.first, SensorDetailsDBColumns::users))
        {
            row.users_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::isRemoteSensor))
        {
            row.isRemoteSensor_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::remoteDeviceId))
        {
            row.remoteDeviceId_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::remoteDeviceName))
        {
            row.remoteDeviceName_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::remoteDeviceLocation))
        {
            row.remoteDeviceLocation_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::httpStatus))
        {
            row.httpStatus_value = stringToLong(column.second);
        }
        else if (iequals(column.first, SensorDetailsDBColumns::sensorStatus))
        {
            row.sensorStatus_value = stringToLong(column.second);
        }
        else if (iequals(column.first, SensorDetailsDBColumns::created_date_time))
        {
            row.created_date_time_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::modified_date_time))
        {
            row.modified_date_time_value = column.second;
        }
    }
}

#ifdef UNIT_TEST
std::vector<VideoRecordDBColumns> Postgresql::getLastRecordVideoRecord(string streamId)
{
    std::vector<VideoRecordDBColumns> rows;
    VideoRecordDBColumns row;
    queryResult result;
    string id = streamId;

    /* Create SQL statement to update the record */
    std::string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name +
    " WHERE ROWID in " + "( SELECT max(ROWID) FROM " + VideoRecordDBColumns::table_name + " WHERE " + VideoRecordDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ");";
    std::vector<std::string> params = {id};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}
#endif

ConnectionPool::ConnectionPool(const std::string& conn_str, int pool_size)
{
    int attempt;
    bool isSuccess;

    for (int cnt = 0; cnt < pool_size; ++cnt)
    {
        attempt = 0;
        isSuccess = false;

        do
        {
            ++attempt;
            try
            {
                auto conn = std::make_shared<pqxx::connection>(conn_str);
                if (conn->is_open())
                {
                    m_connections.push(conn);
                    LOG(info) << "Opened database successfully: " << conn->dbname() << std::endl;
                    isSuccess = true;
                    break;
                }
                else
                {
                    LOG(error) << "Error in creating connection: " << std::endl;
                }
            }
            catch (const std::exception &e)
            {
                LOG(warning) << "Attempt " << attempt << ": Error connecting to PostgreSQL: " << e.what() << std::endl;
            }

            if (!isSuccess && attempt < MAX_CONNECTION_RETRY)
            {
                LOG(info) << "Retrying in " << CONNECTION_RETRY_DELAY << " seconds..." << std::endl;
                std::this_thread::sleep_for(std::chrono::seconds(CONNECTION_RETRY_DELAY));
            }
        } while (!isSuccess && attempt < MAX_CONNECTION_RETRY);

        if (!isSuccess)
        {
            LOG(info) << "No need to create DB connections it seems offline" << std::endl;
            break;
        }
    }
    LOG(info) << "DB connection pool: " << m_connections.size() << " created" << endl;
}

void ConnectionPool::closeAllConnections()
{
    std::unique_lock<std::mutex> lock(m_poolMutex);
    while (!m_connections.empty())
    {
        auto conn = m_connections.front();
        m_connections.pop();
        // The connection will be automatically closed when the shared_ptr is destroyed
        conn.reset();
    }
    LOG(info) << "All connections in the pool have been closed" << std::endl;
}

std::shared_ptr<pqxx::connection> ConnectionPool::getConnection()
{
    std::unique_lock<std::mutex> lock(m_poolMutex);
    if (m_connections.empty())
    {
        LOG(error) << "Connection pool is empty" << endl;
    }
    m_condition.wait(lock, [this] { return !m_connections.empty(); });
    auto conn = m_connections.front();
    m_connections.pop();
    return conn;
}

void ConnectionPool::releaseConnection(std::shared_ptr<pqxx::connection> conn)
{
    std::unique_lock<std::mutex> lock(m_poolMutex);
    m_connections.push(conn);
    m_condition.notify_one();
}

bool ConnectionPool::isPoolCreated()
{
    return m_connections.size() > 0;
}

bool Postgresql::connect()
{
    LOG(verbose) << __PRETTY_FUNCTION__ << endl;
    bool isSuccess = true;

    try
    {
        const auto &config = GET_CONFIG();
        string centralize_db_hostaddr = config.centralize_remote_db_hostaddr;

        // For local connection using Unix domain socket
        if (config.use_centralize_local_db)
        {
            m_connectionString = "dbname=" + config.centralize_db_name +
                            " user=" + config.centralize_db_username +
                            " host=" + config.vst_data_path; // Custom socket directory
        }
        else // For TCP connection
        {
            if (validateIpAddress(centralize_db_hostaddr) == false)
            {
                /* It might be an dns name */
                string resolved_ip = getIpAddrFromDnsName(centralize_db_hostaddr);
                if (!resolved_ip.empty())
                {
                    centralize_db_hostaddr = resolved_ip;
                }
            }
            m_connectionString =
                "dbname=" + config.centralize_db_name +
                " user=" + config.centralize_db_username +
                " password=" + config.centralize_remote_db_password +
                " hostaddr=" + centralize_db_hostaddr +
                " port=" + config.centralize_remote_db_port;
        }

        // Store pool size for future recreations
        m_poolSize = config.max_centralize_db_conn;

        // Establish a connection to the PostgreSQL server
        LOG(info) << "Centralize Database name: " << config.centralize_db_name << endl;
        LOG(info) << "Centralize Database Username: " << maskSensitiveData(config.centralize_db_username, MaskType::USERNAME) << endl;
        LOG(info) << "Centralize Database Password: " << maskSensitiveData(config.centralize_remote_db_password, MaskType::PASSWORD) << endl;
        LOG(info) << "Centralize Database Host address " << centralize_db_hostaddr << endl;
        LOG(info) << "Centralize Database Port " << config.centralize_remote_db_port << endl;
        LOG(info) << "Centralize Database Pool Size: " << config.max_centralize_db_conn << endl;
        LOG(verbose) << "ConnectionStr: " << m_connectionString << endl;

        m_connectionPool = std::make_shared<ConnectionPool>(m_connectionString, config.max_centralize_db_conn);
    }
    catch (const std::exception &e)
    {
        LOG(error) << "Unexpected error: " << e.what() << std::endl;
        isSuccess = false;
    }

    if (m_connectionPool.get() == nullptr || !m_connectionPool->isPoolCreated())
    {
        LOG(error) << "Failed to create connection pool or pool has no valid connections. Exiting application." << std::endl;
        exit(EXIT_FAILURE); // Exit the application after max retries
    }

    return isSuccess;
}

bool Postgresql::recreateConnectionPool()
{
    // Try to acquire the lock without blocking
    std::unique_lock<std::mutex> lock(m_connectionLock, std::try_to_lock);

    // If couldn't get the lock, another thread is already recreating the pool
    if (!lock.owns_lock())
    {
        LOG(info) << "Another thread is already recreating the pool, skipping..." << std::endl;
        return true;
    }

    m_isRecreatingPool = true;

    try
    {
        // Close old pool connections
        if (m_connectionPool)
        {
            LOG(info) << "Closing all connections in the old pool..." << std::endl;
            m_connectionPool->closeAllConnections();
        }

        // Create new pool
        std::shared_ptr<ConnectionPool> newPool = std::make_shared<ConnectionPool>(m_connectionString, m_poolSize);
        LOG(info) << "New pool created successfully" << std::endl;

        // Replace the old pool with the new one
        m_connectionPool = newPool;
        m_isRecreatingPool = false;
        m_poolRecreated.notify_all();
        LOG(info) << "Connection pool recreated successfully" << std::endl;
        return true;
    }
    catch (const std::exception &e)
    {
        m_isRecreatingPool = false;
        m_poolRecreated.notify_all();
        LOG(error) << "Failed to recreate connection pool: " << e.what() << std::endl;
        return false;
    }
}

void Postgresql::safeReleaseConnection(std::shared_ptr<pqxx::connection>& conn)
{
    if (conn != nullptr)
    {
        std::lock_guard<std::mutex> lock(m_connectionLock);
        // Only release connection if pool recreation is not in progress
        if (!m_isRecreatingPool && m_connectionPool)
        {
            m_connectionPool->releaseConnection(conn);
        }
        else
        {
            LOG(warning) << "Skipping connection release - pool recreation in progress or pool is null" << std::endl;
        }
        conn.reset();  // Reset inside the lock to prevent race conditions
    }
}


std::shared_ptr<pqxx::connection> Postgresql::getConnectionFromPool(const std::string& query, int& retryCount)
{
    std::unique_lock<std::mutex> lock(m_connectionLock);

    // Wait if pool is being recreated with timeout
    if (m_isRecreatingPool)
    {
        const auto timeout = std::chrono::seconds(10); // 10 second timeout
        if (!m_poolRecreated.wait_for(lock, timeout, [this] { return !m_isRecreatingPool; }))
        {
            LOG(error) << "Timeout waiting for pool recreation after " << timeout.count() << " seconds" << " for query: " << query << std::endl;
            return nullptr;
        }
        LOG(info) << "Pool recreation completed, proceeding with query: " << query << std::endl;
    }

    // Check pool and get connection atomically
    if (!m_connectionPool || !m_connectionPool->isPoolCreated())
    {
        LOG(warning) << "DB not connected, attempt " << retryCount << " for query: " << query << endl;
        return nullptr;
    }

    auto conn = m_connectionPool->getConnection();
    if (!conn || !conn->is_open())
    {
        LOG(warning) << "Got closed connection from pool, attempt " << retryCount << " for query: " << query << endl;
        return nullptr;
    }

    return conn;
}

bool Postgresql::executeQueryWithConnection(const std::string& query,
                                         std::function<void(pqxx::result&)> resultHandler)
{
    const int MAX_RETRIES = 5;
    int retryCount = 0;
    const int RETRY_DELAY_MS = 2000;

    while (retryCount < MAX_RETRIES)
    {
        auto conn = getConnectionFromPool(query, retryCount);
        if (!conn)
        {
            if (retryCount < MAX_RETRIES - 1)
            {
                std::this_thread::sleep_for(std::chrono::milliseconds(RETRY_DELAY_MS));
            }
            retryCount++;
            continue;
        }

        std::unique_ptr<pqxx::work> txn;
        try
        {
            txn = std::make_unique<pqxx::work>(*conn);
            pqxx::result QueryResult = txn->exec(query);

            if (resultHandler)
            {
                resultHandler(QueryResult);
            }

            txn->commit();
            safeReleaseConnection(conn);
            return true;
        }
        catch (const pqxx::sql_error &e)
        {
            if (txn)
            {
                txn->abort();
            }
            LOG(error) << "Error executing query - Message: " << e.what() << ", query: " << query << std::endl;
            safeReleaseConnection(conn);
            return false;
        }
        catch (const pqxx::broken_connection &e)
        {
            if (txn)
            {
                txn->abort();
            }
            LOG(warning) << "Connection broken during query execution - Message: " << e.what() << " query: " << query << std::endl;

            if (conn)
            {
                conn.reset();  // This will automatically clean up the connection
            }

            // Recreate the connection pool
            if (recreateConnectionPool())
            {
                LOG(info) << "Connection pool recreated successfully" << std::endl;

                if (retryCount < MAX_RETRIES - 1)
                {
                    retryCount++;
                    continue;
                }
            }
            return false;
        }
        catch (const std::exception &e)
        {
            if (txn)
            {
                txn->abort();
            }
            LOG(error) << "Error executing query - Message: " << e.what() << ", query: " << query << std::endl;
            safeReleaseConnection(conn);
            return false;
        }
    }

    LOG(error) << "Failed to execute query after " << retryCount << " retries for query: " << query << std::endl;
    return false;
}

// Simplified executeQuery APIs
bool Postgresql::executeQuery(const std::string &query, queryResult &result)
{
    auto resultHandler = [&result](pqxx::result& QueryResult) {
        for (const auto &row : QueryResult)
        {
            std::unordered_map<std::string, std::string> rowData;
            for (std::size_t colIndex = 0; colIndex < (size_t)row.size(); ++colIndex)
            {
                rowData[QueryResult.column_name(colIndex)] = row.at(static_cast<int>(colIndex)).c_str();
            }
            result.push_back(rowData);
        }
    };

    return executeQueryWithConnection(query, resultHandler);
}

bool Postgresql::executeQuery(const std::string &query)
{
    return executeQueryWithConnection(query, nullptr);
}

// Parameterized query methods to prevent SQL injection
bool Postgresql::executeQuery(const std::string &queryTemplate, const std::vector<std::string> &params, queryResult &result)
{
    // Build the safe query using QueryBuilder with PostgreSQL-specific escaping
    std::string safeQuery = QueryBuilder::buildQuery(queryTemplate, params, DatabaseType::PostgreSQL);
    return executeQuery(safeQuery, result);
}

bool Postgresql::executeQuery(const std::string &queryTemplate, const std::vector<std::string> &params)
{
    // Build the safe query using QueryBuilder with PostgreSQL-specific escaping
    std::string safeQuery = QueryBuilder::buildQuery(queryTemplate, params, DatabaseType::PostgreSQL);
    return executeQuery(safeQuery);
}

bool Postgresql::isConnected()
{
    std::lock_guard<std::mutex> lock(m_connectionLock);
    try
    {
        return m_connectionPool != nullptr;
    }
    catch (const std::exception &e)
    {
        LOG(error) << "Error checking connection status: " << e.what() << std::endl;
        return false;
    }
}

int Postgresql::insertRowEvent(EventDBColumns &row)
{
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement with parameterized query using safe macros */
    std::string queryTemplate = "INSERT INTO " + EventDBColumns::table_name + "(";
    
    APPEND_COLUMN(EventDBColumns::video_path, row.video_path_value, queryTemplate)
    APPEND_COLUMN(DBColumns::device_id, row.device_id_value, queryTemplate)
    APPEND_COLUMN(DBColumns::sensor_id, row.sensor_id_value, queryTemplate)
    APPEND_COLUMN(EventDBColumns::start_time, row.start_time_value, queryTemplate)
    APPEND_COLUMN(EventDBColumns::end_time, row.end_time_value, queryTemplate)
    APPEND_COLUMN(EventDBColumns::event_name, row.event_name_value, queryTemplate)
    APPEND_COLUMN(EventDBColumns::event_id, row.event_id_value, queryTemplate)
    APPEND_COLUMN(DBColumns::created_date_time, currentUtcTime, queryTemplate)
    APPEND_COLUMN(DBColumns::modified_date_time, currentUtcTime, queryTemplate)
    queryTemplate.pop_back(); // Remove trailing comma
    
    // Build parameterized values using safe macros
    std::vector<std::string> params;
    APPEND_COLUMN_VALUE(row.video_path_value, params)
    APPEND_COLUMN_VALUE(row.device_id_value, params)
    APPEND_COLUMN_VALUE(row.sensor_id_value, params)
    APPEND_COLUMN_VALUE(row.start_time_value, params)
    APPEND_COLUMN_VALUE(row.end_time_value, params)
    APPEND_COLUMN_VALUE(row.event_name_value, params)
    APPEND_COLUMN_VALUE(row.event_id_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(queryTemplate, params);
    queryTemplate += ";";

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Postgresql::insertRowSensorDetails(SensorDetailsDBColumns &row)
{
    string currentUtcTime = getCurrentUtcTime();
    string encryptedPassword = EMPTY_STRING;
    SensorDetailsDBColumns r = readSensorDetails(row.device_id_value, row.sensor_id_value);
    row.sensor_hw_id_value = row.sensor_hw_id_value.empty() ? r.sensor_hw_id_value : row.sensor_hw_id_value;
    row.username_value = row.username_value.empty() ? r.username_value : row.username_value;
    row.password_value = row.password_value.empty() ? r.password_value : row.password_value;
    if (!row.password_value.empty())
    {
        if (!vst_common::encrypt_data(row.password_value, encryptedPassword, row.sensor_id_value))
        {
            LOG(error) << "Failed to encrypt password for sensor: " << row.sensor_id_value << endl;
            encryptedPassword = EMPTY_STRING; // Ensure we don't store corrupted data
        }
    }
    row.name_value = row.name_value.empty() ? r.name_value : row.name_value;
    row.ip_value = row.ip_value.empty() ? r.ip_value : row.ip_value;
    row.hardware_value = row.hardware_value.empty() ? r.hardware_value : row.hardware_value;
    row.manufacturer_value = row.manufacturer_value.empty() ? r.manufacturer_value : row.manufacturer_value;
    row.serial_number_value = row.serial_number_value.empty() ? r.serial_number_value : row.serial_number_value;
    row.firmware_version_value = row.firmware_version_value.empty() ? r.firmware_version_value : row.firmware_version_value;
    row.hardware_id_value = row.hardware_id_value.empty() ? r.hardware_id_value : row.hardware_id_value;
    row.location_value = row.location_value.empty() ? r.location_value : row.location_value;
    row.tags_value = row.tags_value.empty() ? r.tags_value : row.tags_value;
    row.url_value = row.url_value.empty() ? r.url_value : row.url_value;
    row.type_value = row.type_value.empty() ? r.type_value : row.type_value;
    row.position_value = row.position_value.empty() ? r.position_value : row.position_value;
    row.users_value = row.users_value.empty() ? r.users_value : row.users_value;
    row.isRemoteSensor_value = row.isRemoteSensor_value.empty() ? r.isRemoteSensor_value : row.isRemoteSensor_value;
    row.remoteDeviceId_value = row.remoteDeviceId_value.empty() ? r.remoteDeviceId_value : row.remoteDeviceId_value;
    row.remoteDeviceName_value = row.remoteDeviceName_value.empty() ? r.remoteDeviceName_value : row.remoteDeviceName_value;
    row.remoteDeviceLocation_value = row.remoteDeviceLocation_value.empty() ? r.remoteDeviceLocation_value : row.remoteDeviceLocation_value;
    row.httpStatus_value = row.httpStatus_value == -1 ? r.httpStatus_value : row.httpStatus_value;
    row.sensorStatus_value = row.sensorStatus_value == SensorStatusUnknown ? r.sensorStatus_value : row.sensorStatus_value;
    row.created_date_time_value = r.created_date_time_value.empty() ? currentUtcTime : r.created_date_time_value;
    row.modified_date_time_value = currentUtcTime;

    /* Create SQL statement using parameterized query to prevent SQL injection */
    std::string queryTemplate = "INSERT INTO " + SensorDetailsDBColumns::table_name + "(" +
                      DBColumns::device_id + "," + DBColumns::sensor_id + "," +
                      SensorDetailsDBColumns::sensor_hw_id + "," +
                      SensorDetailsDBColumns::username + "," +
                      SensorDetailsDBColumns::password + "," +
                      SensorDetailsDBColumns::name + "," +
                      SensorDetailsDBColumns::ip + "," +
                      SensorDetailsDBColumns::hardware + "," +
                      SensorDetailsDBColumns::manufacturer + "," +
                      SensorDetailsDBColumns::serial_number + "," +
                      SensorDetailsDBColumns::firmware_version + "," +
                      SensorDetailsDBColumns::hardware_id + "," +
                      SensorDetailsDBColumns::location + "," +
                      SensorDetailsDBColumns::tags + "," +
                      SensorDetailsDBColumns::url + "," +
                      SensorDetailsDBColumns::type + "," +
                      SensorDetailsDBColumns::position + "," +
                      SensorDetailsDBColumns::users + "," +
                      SensorDetailsDBColumns::isRemoteSensor + "," +
                      SensorDetailsDBColumns::remoteDeviceId + "," +
                      SensorDetailsDBColumns::remoteDeviceName + "," +
                      SensorDetailsDBColumns::remoteDeviceLocation + "," +
                      SensorDetailsDBColumns::httpStatus + "," +
                      SensorDetailsDBColumns::sensorStatus + "," +
                      SensorDetailsDBColumns::created_date_time + "," +
                      SensorDetailsDBColumns::modified_date_time;

                      std::vector<std::string> params = {
                        row.device_id_value, row.sensor_id_value, row.sensor_hw_id_value,
                        row.username_value, encryptedPassword, row.name_value, row.ip_value,
                        row.hardware_value, row.manufacturer_value, row.serial_number_value,
                        row.firmware_version_value, row.hardware_id_value, row.location_value,
                        row.tags_value, row.url_value, row.type_value, row.position_value,
                        row.users_value, row.isRemoteSensor_value, row.remoteDeviceId_value,
                        row.remoteDeviceName_value, row.remoteDeviceLocation_value,
                        std::to_string(row.httpStatus_value), std::to_string(row.sensorStatus_value),
                        row.created_date_time_value, row.modified_date_time_value
                      };

                      // Use BUILD_VALUES_CLAUSE to add the VALUES clause with parameter placeholders
                      BUILD_VALUES_CLAUSE(queryTemplate, params);

                      // Add the ON CONFLICT clause
                      queryTemplate += " ON CONFLICT (" + DBColumns::sensor_id + ") DO UPDATE SET " +
                      SensorDetailsDBColumns::sensor_hw_id + "=EXCLUDED." + SensorDetailsDBColumns::sensor_hw_id + "," +
                      SensorDetailsDBColumns::username + "=EXCLUDED." + SensorDetailsDBColumns::username + "," +
                      SensorDetailsDBColumns::password + "=EXCLUDED." + SensorDetailsDBColumns::password + "," +
                      SensorDetailsDBColumns::name + "=EXCLUDED." + SensorDetailsDBColumns::name + "," +
                      SensorDetailsDBColumns::ip + "=EXCLUDED." + SensorDetailsDBColumns::ip + "," +
                      SensorDetailsDBColumns::hardware + "=EXCLUDED." + SensorDetailsDBColumns::hardware + "," +
                      SensorDetailsDBColumns::manufacturer + "=EXCLUDED." + SensorDetailsDBColumns::manufacturer + "," +
                      SensorDetailsDBColumns::serial_number + "=EXCLUDED." + SensorDetailsDBColumns::serial_number + "," +
                      SensorDetailsDBColumns::firmware_version + "=EXCLUDED." + SensorDetailsDBColumns::firmware_version + "," +
                      SensorDetailsDBColumns::hardware_id + "=EXCLUDED." + SensorDetailsDBColumns::hardware_id + "," +
                      SensorDetailsDBColumns::location + "=EXCLUDED." + SensorDetailsDBColumns::location + "," +
                      SensorDetailsDBColumns::tags + "=EXCLUDED." + SensorDetailsDBColumns::tags + "," +
                      SensorDetailsDBColumns::url + "=EXCLUDED." + SensorDetailsDBColumns::url + "," +
                      SensorDetailsDBColumns::type + "=EXCLUDED." + SensorDetailsDBColumns::type + "," +
                      SensorDetailsDBColumns::position + "=EXCLUDED." + SensorDetailsDBColumns::position + "," +
                      SensorDetailsDBColumns::users + "=EXCLUDED." + SensorDetailsDBColumns::users + "," +
                      SensorDetailsDBColumns::isRemoteSensor + "=EXCLUDED." + SensorDetailsDBColumns::isRemoteSensor + "," +
                      SensorDetailsDBColumns::remoteDeviceId + "=EXCLUDED." + SensorDetailsDBColumns::remoteDeviceId + "," +
                      SensorDetailsDBColumns::remoteDeviceName + "=EXCLUDED." + SensorDetailsDBColumns::remoteDeviceName + "," +
                      SensorDetailsDBColumns::remoteDeviceLocation + "=EXCLUDED." + SensorDetailsDBColumns::remoteDeviceLocation + "," +
                      SensorDetailsDBColumns::httpStatus + "=EXCLUDED." + SensorDetailsDBColumns::httpStatus + "," +
                      SensorDetailsDBColumns::sensorStatus + "=EXCLUDED." + SensorDetailsDBColumns::sensorStatus + "," +
                      SensorDetailsDBColumns::created_date_time + "=EXCLUDED." + SensorDetailsDBColumns::created_date_time + "," +
                      SensorDetailsDBColumns::modified_date_time + "=EXCLUDED." + SensorDetailsDBColumns::modified_date_time + ";";

    LOG(verbose) << "SQL query template: " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

SensorDetailsDBColumns Postgresql::readSensorDetails(string deviceId, string sensorId)
{
    SensorDetailsDBColumns row;
    queryResult result;
    string id = sensorId;
    
    std::string queryTemplate = "SELECT * FROM " + SensorDetailsDBColumns::table_name + " WHERE " + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {id};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        sensorDetailsHelper(row, entries);
    }
    return row;
}

SensorDetailsDBColumns Postgresql::readSensorDetailsByLocation(string location)
{
    SensorDetailsDBColumns row;
    queryResult result;
    
    std::string queryTemplate = "SELECT * FROM " + SensorDetailsDBColumns::table_name +
                 " WHERE " + SensorDetailsDBColumns::location + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {location};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        sensorDetailsHelper(row, entries);
    }
    return row;
}

vector<SensorDetailsDBColumns> Postgresql::readSensorDetails(string deviceId)
{
    vector<SensorDetailsDBColumns> rowArray;

    string queryTemplate;
    queryResult result;
    std::vector<std::string> params;
    
    if (deviceId.empty() == false)
    {
        queryTemplate = "SELECT * FROM " + SensorDetailsDBColumns::table_name +
              " WHERE " + DBColumns::device_id + " = " + PARAM_PLACEHOLDER(0) + ";";
        params = {deviceId};
    }
    else
    {
        queryTemplate = "SELECT * FROM " + SensorDetailsDBColumns::table_name + ";";
    }
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rowArray;
    }

    for (auto entries : result)
    {
        SensorDetailsDBColumns row;
        sensorDetailsHelper(row, entries);
        rowArray.push_back(row);
    }
    return rowArray;
}

int Postgresql::CountSensorDetails(string deviceId)
{
    string queryTemplate;
    queryResult result;
    std::vector<std::string> params;

    if (!deviceId.empty())
    {
        queryTemplate = "SELECT COUNT(*) FROM " + SensorDetailsDBColumns::table_name +
              " WHERE " + DBColumns::device_id + " = " + PARAM_PLACEHOLDER(0) + ";";
        params = {deviceId};
    }
    else
    {
        queryTemplate = "SELECT COUNT(*) FROM " + SensorDetailsDBColumns::table_name + ";";
    }
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }

    if (result.size() > 0 && result[0].count("count") > 0)
    {
        return std::stoi(result[0]["count"]);
    }

    return 0;
}

vector<SensorDetailsDBColumns> Postgresql::readAllSensorSatus(string deviceId)
{
    vector<SensorDetailsDBColumns> rowArray;

    string queryTemplate;
    queryResult result;
    std::vector<std::string> params;
    
    if (deviceId.empty() == false)
    {
        queryTemplate = "SELECT " + SensorDetailsDBColumns::sensor_id + ", " + SensorDetailsDBColumns::httpStatus + ", " + SensorDetailsDBColumns::sensorStatus + " FROM " + SensorDetailsDBColumns::table_name +
              " WHERE " + DBColumns::device_id + " = " + PARAM_PLACEHOLDER(0) + ";";
        params = {deviceId};
    }
    else
    {
        queryTemplate = "SELECT " + SensorDetailsDBColumns::sensor_id + ", " + SensorDetailsDBColumns::httpStatus + ", " + SensorDetailsDBColumns::sensorStatus + " FROM " + SensorDetailsDBColumns::table_name;
    }
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rowArray;
    }

    for (auto entries : result)
    {
        SensorDetailsDBColumns row;
        sensorDetailsHelper(row, entries);
        rowArray.push_back(row);
    }
    return rowArray;
}

int Postgresql::deleteSensorDetails(string sensorId)
{
    // Execute all deletions in a single transaction to avoid foreign key constraint violations
    string id = sensorId;
    
    // Build the complete deletion query as a single transaction
    std::string queryTemplate = 
        "DELETE FROM " + SensorStreamsDBColumns::table_name + " WHERE " + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + "; "
        "DELETE FROM " + RecordingStatusDBColumns::table_name + " WHERE " + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + "; "
        "DELETE FROM " + SensorDetailsDBColumns::table_name + " WHERE " + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    
    std::vector<std::string> params = {id, id, id};
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;  // Return 0 on success
}

int Postgresql::insertRowVideoRecord(VideoRecordDBColumns &row)
{
    string id = row.sensor_id_value;
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement with parameterized query using safe macros */
    std::string queryTemplate = "INSERT INTO " + VideoRecordDBColumns::table_name + "(";
    
    APPEND_COLUMN(DBColumns::sensor_id, row.sensor_id_value, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::stream_id, row.stream_id_value, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::resolution, row.resolution_value, queryTemplate)
    APPEND_COLUMN_INT(VideoRecordDBColumns::start_time, row.start_time_value, queryTemplate)
    APPEND_COLUMN_INT(VideoRecordDBColumns::duration, row.duration_value, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::file_path, row.filepath_value, queryTemplate)
    APPEND_COLUMN_INT(VideoRecordDBColumns::file_size, row.filesize_value, queryTemplate)
    APPEND_COLUMN_INT(VideoRecordDBColumns::file_fps, row.filefps_value, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::sensor_name, row.sensor_name_value, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::record_config, row.record_config_value, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::codec, row.codec_value, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::file_protection, row.file_protection_value, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::metadata_file_path, row.metadata_file_path_value, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::metadata_json, row.metadata_json_value, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::object_id, row.object_id_value, queryTemplate)
    APPEND_COLUMN_INT(VideoRecordDBColumns::storage_location, row.storage_location_value, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::bucket_name, row.bucket_name_value, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::created_date_time, currentUtcTime, queryTemplate)
    APPEND_COLUMN(VideoRecordDBColumns::modified_date_time, currentUtcTime, queryTemplate)
    queryTemplate.pop_back(); // Remove trailing comma
    
    // Build parameterized values using safe macros
    std::vector<std::string> params;
    APPEND_COLUMN_VALUE(row.sensor_id_value, params)
    APPEND_COLUMN_VALUE(row.stream_id_value, params)
    APPEND_COLUMN_VALUE(row.resolution_value, params)
    APPEND_COLUMN_VALUE_INT(row.start_time_value, params)
    APPEND_COLUMN_VALUE_INT(row.duration_value, params)
    APPEND_COLUMN_VALUE(row.filepath_value, params)
    APPEND_COLUMN_VALUE_INT(row.filesize_value, params)
    APPEND_COLUMN_VALUE_INT(row.filefps_value, params)
    APPEND_COLUMN_VALUE(row.sensor_name_value, params)
    APPEND_COLUMN_VALUE(row.record_config_value, params)
    APPEND_COLUMN_VALUE(row.codec_value, params)
    APPEND_COLUMN_VALUE(row.file_protection_value, params)
    APPEND_COLUMN_VALUE(row.metadata_file_path_value, params)
    APPEND_COLUMN_VALUE(row.metadata_json_value, params)
    APPEND_COLUMN_VALUE(row.object_id_value, params)
    APPEND_COLUMN_VALUE_INT(row.storage_location_value, params)
    APPEND_COLUMN_VALUE(row.bucket_name_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(queryTemplate, params);
    queryTemplate += ";";

    LOG(verbose) << "SQL query template: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

/* File Type Sensor Usecase for ReplayStream */
std::vector<VideoRecordDBColumns> Postgresql::readVideoRecordStreamIdBased(string sensorId, int64_t startTime, int64_t endTime)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;

    std::string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
            VideoRecordDBColumns::stream_id +
            " = " + PARAM_PLACEHOLDER(0) + "" +
            " AND (" + VideoRecordDBColumns::start_time + " + " +
            VideoRecordDBColumns::duration + " >= " + PARAM_PLACEHOLDER(1) + " OR " +
            VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + " )" + " AND " +
            VideoRecordDBColumns::start_time + " < " + PARAM_PLACEHOLDER(2) + " AND " +
            VideoRecordDBColumns::duration + " != " + PARAM_PLACEHOLDER(3) + ";";
    std::vector<std::string> params = {sensorId, to_string(startTime), to_string(endTime), std::to_string(FILE_INIT_DURATION)};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }

    // Sort the rows locally based on start_time
    std::sort(rows.begin(), rows.end(), [](const VideoRecordDBColumns& a, const VideoRecordDBColumns& b) {
        return a.start_time_value < b.start_time_value;
    });

    return rows;
}

/* File Type Sensor Usecase for Storage Service Download when ONLY Sensor ID is provided */
std::vector<VideoRecordDBColumns> Postgresql::readVideoRecordSensorIdBased(string sensorId, int64_t startTime, int64_t endTime)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;

    std::string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
            VideoRecordDBColumns::stream_id +
            " = " + PARAM_PLACEHOLDER(0) + "" +
            " AND (" + VideoRecordDBColumns::start_time + " + " +
            VideoRecordDBColumns::duration + " >= " + PARAM_PLACEHOLDER(1) + " OR " +
            VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + " )" + " AND " +
            VideoRecordDBColumns::start_time + " < " + PARAM_PLACEHOLDER(2) + " AND " +
            VideoRecordDBColumns::duration + " != " + PARAM_PLACEHOLDER(3) + ";";
    std::vector<std::string> params = {sensorId, to_string(startTime), to_string(endTime), std::to_string(FILE_INIT_DURATION)};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }

    // Sort the rows locally based on start_time
    std::sort(rows.begin(), rows.end(), [](const VideoRecordDBColumns& a, const VideoRecordDBColumns& b) {
        return a.start_time_value < b.start_time_value;
    });

    return rows;
}

std::vector<VideoRecordDBColumns> Postgresql::readVideoRecord(string sensorId, int64_t startTime, int64_t endTime, const std::vector<string>& streamIds)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;
    bool is_sensor_id = false;
    std::string queryTemplate;
    std::vector<std::string> params;
    
    if (streamIds.size())
    {
        // Use QueryBuilder to safely build WHERE IN clause to prevent SQL injection (PostgreSQL-specific)
        std::string whereInClause = QueryBuilder::buildWhereInClause(VideoRecordDBColumns::stream_id, streamIds, DatabaseType::PostgreSQL);
        if (whereInClause.empty())
        {
            LOG(error) << "Invalid column name: " << VideoRecordDBColumns::stream_id << endl;
            return rows;
        }
        queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " " + whereInClause + ";";
    }
    /* If given stream id is empty then API will return all the video record details */
    else if (sensorId.empty())
    {
        queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + ";";
    }
    else
    {
        queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
                (is_sensor_id ? VideoRecordDBColumns::sensor_id : VideoRecordDBColumns::stream_id) +
                " = " + PARAM_PLACEHOLDER(0) +
                " AND (" + VideoRecordDBColumns::start_time + " + " +
                VideoRecordDBColumns::duration + " >= " + PARAM_PLACEHOLDER(1) + " OR " +
                VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + " )" + " AND " +
                VideoRecordDBColumns::start_time + " <= " + PARAM_PLACEHOLDER(2) + ";";
        params = {sensorId, to_string(startTime), to_string(endTime)};
    }
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        if (row.duration_value == FILE_INIT_DURATION)
        {
            int64_t currentTime = getCurrentUnixTimestampInMs();
            int64_t timeSinceStart = currentTime - row.start_time_value;
            if (timeSinceStart >= 0 && timeSinceStart < (2 * TYPICAL_FILE_DURATION_MS_INT))
            {
                row.duration_value = timeSinceStart;
            }
            else
            {
                row.duration_value = TYPICAL_FILE_DURATION_MS_INT;
            }
        }
        rows.push_back(row);
    }

    // Sort the rows locally based on start_time
    std::sort(rows.begin(), rows.end(), [](const VideoRecordDBColumns& a, const VideoRecordDBColumns& b) {
        return a.start_time_value < b.start_time_value;
    });

    return rows;
}

/* File Type Sensor Usecase for Storage Service Download when ONLY Object ID is provided */
std::vector<VideoRecordDBColumns> Postgresql::readVideoRecordUniqueIdBased(string id)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;

    /* If given stream id is empty then API will return all the video record details */
    std::string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " + VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {id};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        /*
         * To fix.
         * Commenting due to bug 3945965.
         */
        if (row.duration_value == FILE_INIT_DURATION)
        {
            // updateActualDuration(row);
            LOG(verbose) << "Trying to get correct duration of file either open or improperly closed" << endl;
            vst_storage::addOrRemoveFileInProtectList(row.filepath_value, true);
            guint64 duration = getMediaFileDuration(row.filepath_value);
            if (duration)
            {
                row.duration_value = duration;
            }
            else
            {
                LOG(error) << "Get correct duration pipeline failed" << endl;
            }
            vst_storage::addOrRemoveFileInProtectList(row.filepath_value, false);
        }
        rows.push_back(row);
    }

    // Sort the rows locally based on start_time
    std::sort(rows.begin(), rows.end(), [](const VideoRecordDBColumns& a, const VideoRecordDBColumns& b) {
        return a.start_time_value < b.start_time_value;
    });

    return rows;
}

/* File Type Sensor Usecase for Storage Service Download when both Sensor ID and Object ID are provided */
std::vector<VideoRecordDBColumns> Postgresql::readVideoRecordSensorIdUniqueIdBased(string sensorId, string id)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;

    std::string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
            VideoRecordDBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + " AND " +
            VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(1) + ";";
    std::vector<std::string> params = {sensorId, id};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        /*
         * To fix.
         * Commenting due to bug 3945965.
         */
        if (row.duration_value == FILE_INIT_DURATION)
        {
            // updateActualDuration(row);
            LOG(verbose) << "Trying to get correct duration of file either open or improperly closed" << endl;
            vst_storage::addOrRemoveFileInProtectList(row.filepath_value, true);
            guint64 duration = getMediaFileDuration(row.filepath_value);
            if (duration)
            {
                row.duration_value = duration;
            }
            else
            {
                LOG(error) << "Get correct duration pipeline failed" << endl;
            }
            vst_storage::addOrRemoveFileInProtectList(row.filepath_value, false);
        }
        rows.push_back(row);
    }

    // Sort the rows locally based on start_time
    std::sort(rows.begin(), rows.end(), [](const VideoRecordDBColumns& a, const VideoRecordDBColumns& b) {
        return a.start_time_value < b.start_time_value;
    });

    return rows;
}

VideoRecordDBColumns Postgresql::readInProgressVideoRecord(string streamId, int64_t startTime)
{
    VideoRecordDBColumns row;
    queryResult result;
    string id = streamId;

    std::string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name +
                 " WHERE " + VideoRecordDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + "" +
                 " AND (" + VideoRecordDBColumns::start_time + " + " +
                 "" + PARAM_PLACEHOLDER(2) + " > " + PARAM_PLACEHOLDER(1) + ")" +
                 " AND (" + VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + "" +
                 " OR " + VideoRecordDBColumns::start_time + " < " + PARAM_PLACEHOLDER(1) + ")" +
                 " AND (" + VideoRecordDBColumns::duration + " = '1');";
    std::vector<std::string> params = {id, std::to_string(startTime), TYPICAL_FILE_DURATION_MAX_MS};
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        videoRecordHelper(row, entries);
    }
    return row;
}

VideoRecordDBColumns Postgresql::readVideoRecordExactMatch(string streamId, int64_t startTime)
{
    VideoRecordDBColumns row;
    queryResult result;
    
    std::string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name +
                 " WHERE " + VideoRecordDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + "" +
                 " AND " + VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + ";";
    std::vector<std::string> params = {streamId, std::to_string(startTime)};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        videoRecordHelper(row, entries);
    }
    return row;
}

VideoRecordDBColumns Postgresql::readVideoRecordExactMatchFilePath(string sensorId, string filePath, int64_t startTime)
{
    VideoRecordDBColumns row;
    queryResult result;
    
    std::string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name +
                 " WHERE " + VideoRecordDBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + "" +
                 " AND " + VideoRecordDBColumns::file_path + " = " + PARAM_PLACEHOLDER(1) + "" +
                 " AND " + VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(2) + ";";
    std::vector<std::string> params = {sensorId, filePath, std::to_string(startTime)};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        videoRecordHelper(row, entries);
    }
    return row;
}

int Postgresql::updateVideoRecordInDb(VideoRecordDBColumns &row)
{
    string filename = row.filepath_value;
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement to update the record */
    std::string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name +
                 " SET " + VideoRecordDBColumns::duration + " = " + PARAM_PLACEHOLDER(0) + "" +
                 " , " + VideoRecordDBColumns::file_size + " = " + PARAM_PLACEHOLDER(1) + "" +
                 " , " + VideoRecordDBColumns::file_fps + " = " + PARAM_PLACEHOLDER(2) + "" +
                 " , " + VideoRecordDBColumns::file_protection + " = " + PARAM_PLACEHOLDER(3) + "" +
                 " , " + VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(4) + "" +
                 " , " + VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(5) + "" +
                 " WHERE " + VideoRecordDBColumns::file_path + " = " + PARAM_PLACEHOLDER(6) + ";";
    
    std::vector<std::string> params = {
        std::to_string(row.duration_value),
        std::to_string(row.filesize_value),
        std::to_string(row.filefps_value),
        row.file_protection_value,
        row.object_id_value,
        currentUtcTime,
        filename
    };

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Postgresql::updateVideoRecordDurationBatch(const std::vector<VideoRecordDBColumns> &rows)
{
    if (rows.empty())
    {
        LOG(verbose) << "No rows to update in batch" << endl;
        return 0;
    }

    // Filter rows with valid duration (> 0)
    std::vector<std::pair<std::string, guint64>> validRows;
    for (const auto &row : rows)
    {
        if (row.duration_value > 0)
        {
            validRows.emplace_back(row.filepath_value, row.duration_value);
        }
    }

    if (validRows.empty())
    {
        LOG(verbose) << "No rows with valid duration to update" << endl;
        return 0;
    }

    string currentUtcTime = getCurrentUtcTime();

    // Build VALUES list for batch update using parameterized escaping
    // Query format: UPDATE table AS t SET duration = v.duration, modified_date_time = {0}
    //               FROM (VALUES ('path1', 1000), ('path2', 2000)) AS v(file_path, duration)
    //               WHERE t.FILE_PATH = v.file_path;
    std::string valuesList;
    for (size_t i = 0; i < validRows.size(); ++i)
    {
        if (i > 0)
        {
            valuesList += ", ";
        }
        valuesList += "(" + QueryBuilder::escapeString(validRows[i].first) + ", " + std::to_string(validRows[i].second) + ")";
    }

    // Build UPDATE query using parameterized query for modified_date_time
    std::string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name + " AS t "
                        "SET " + VideoRecordDBColumns::duration + " = v.duration, "
                        + VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(0) + " "
                        "FROM (VALUES " + valuesList + ") AS v(file_path, duration) "
                        "WHERE t." + VideoRecordDBColumns::file_path + " = v.file_path;";

    LOG(verbose) << "Batch update SQL query for " << validRows.size() << " rows" << endl;

    if (!executeQuery(queryTemplate, {currentUtcTime}))
    {
        LOG(error) << "Error executing batch update SQL stmt" << endl;
        return -1;
    }

    LOG(info) << "Batch updated duration for " << validRows.size() << " video records" << endl;
    return static_cast<int>(validRows.size());
}

int Postgresql::insertRowVideoRecordSchedule(VideoRecordScheduleDBColumns &row)
{

    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement using parameterized query to prevent SQL injection */
    /* Create SQL statement with parameterized query using safe macros */
    std::string queryTemplate = "INSERT INTO " + VideoRecordScheduleDBColumns::table_name + "(";
    
    APPEND_COLUMN(DBColumns::device_id, row.device_id_value, queryTemplate)
    APPEND_COLUMN(DBColumns::sensor_id, row.sensor_id_value, queryTemplate)
    APPEND_COLUMN(VideoRecordScheduleDBColumns::stream_id, row.stream_id_value, queryTemplate)
    APPEND_COLUMN(VideoRecordScheduleDBColumns::start_time, row.start_time_value, queryTemplate)
    APPEND_COLUMN(VideoRecordScheduleDBColumns::end_time, row.end_time_value, queryTemplate)
    APPEND_COLUMN(VideoRecordScheduleDBColumns::created_date_time, currentUtcTime, queryTemplate)
    APPEND_COLUMN(VideoRecordScheduleDBColumns::modified_date_time, currentUtcTime, queryTemplate)
    queryTemplate.pop_back(); // Remove trailing comma
    
    // Build parameterized values using safe macros
    std::vector<std::string> params;
    APPEND_COLUMN_VALUE(row.device_id_value, params)
    APPEND_COLUMN_VALUE(row.sensor_id_value, params)
    APPEND_COLUMN_VALUE(row.stream_id_value, params)
    APPEND_COLUMN_VALUE(row.start_time_value, params)
    APPEND_COLUMN_VALUE(row.end_time_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(queryTemplate, params);
    queryTemplate += ";";

    LOG(verbose) << "SQL query template: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }

    return 0;
}

std::vector<VideoRecordScheduleDBColumns> Postgresql::readVideoRecordSchedules(string streamId)
{
    vector<VideoRecordScheduleDBColumns> rowArray;
    queryResult result;
    std::string queryTemplate;
    std::vector<std::string> params;

    if (streamId.empty() == false)
    {
        queryTemplate = "SELECT * FROM " + VideoRecordScheduleDBColumns::table_name +
              " WHERE " + VideoRecordScheduleDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ";";
        params = {streamId};
    }
    else
    {
        queryTemplate = "SELECT * FROM " + VideoRecordScheduleDBColumns::table_name + ";";
    }

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rowArray;
    }

    for (auto entries : result)
    {
        VideoRecordScheduleDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, VideoRecordScheduleDBColumns::device_id))
            {
                row.device_id_value = column.second;
            }
            else if (iequals(column.first, VideoRecordScheduleDBColumns::sensor_id))
            {
                row.sensor_id_value = column.second;
            }
            else if (iequals(column.first, VideoRecordScheduleDBColumns::stream_id))
            {
                row.stream_id_value = column.second;
            }
            else if (iequals(column.first, VideoRecordScheduleDBColumns::start_time))
            {
                row.start_time_value = column.second;
            }
            else if (iequals(column.first, VideoRecordScheduleDBColumns::end_time))
            {
                row.end_time_value = column.second;
            }
            else if (iequals(column.first, VideoRecordScheduleDBColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, VideoRecordScheduleDBColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
        }
        rowArray.push_back(row);
    }
    return rowArray;
}

bool Postgresql::deleteVideoRecordSchedule(string streamId, string startTime, string endTime)
{
    VideoRecordScheduleDBColumns row;

    std::string queryTemplate = "DELETE FROM " + VideoRecordScheduleDBColumns::table_name +
                 " WHERE " + VideoRecordScheduleDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + "" +
                 " AND " + VideoRecordScheduleDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + "" +
                 " AND " + VideoRecordScheduleDBColumns::end_time + " = " + PARAM_PLACEHOLDER(2) + ";";
    std::vector<std::string> params = {streamId, startTime, endTime};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return false;
    }
    return true;
}

int Postgresql::deleteVideoRecordings(vector<string> &filePaths)
{
    int ret = 0;
    int j = 0;
    vector<string> filePathsBatch;
    for (uint32_t i = 0; i < filePaths.size(); i++)
    {
        filePathsBatch.push_back(filePaths[i]);
        j++;
        /* serialize and delete entries in batch */
        if (FILEPATH_BATCH_SIZE == j || i == filePaths.size() - 1)
        {
            // Build the complete WHERE clause using QueryBuilder (PostgreSQL-specific)
            std::string whereClause = QueryBuilder::buildWhereInClause(VideoRecordDBColumns::file_path, filePathsBatch, DatabaseType::PostgreSQL);
            if (whereClause.empty())
            {
                LOG(error) << "Invalid column name: " << VideoRecordDBColumns::file_path << endl;
                return -1;
            }
        
            std::string safeQuery = "DELETE FROM " + VideoRecordDBColumns::table_name + " " + whereClause;

        LOG(verbose) << "SQL query: " << safeQuery << endl;

        /* Execute SQL statement */
        if (!executeQuery(safeQuery))
        {
            LOG(error) << "Error executing SQL stmt: " << safeQuery << endl;
            return -1;
        }
            filePathsBatch.clear();
            j = 0;
        }
    }
    return ret;
}

std::vector<VideoRecordDBColumns> Postgresql::readRecordsInBatch(uint32_t &batchSize, bool excludeCloudScanned)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;
    std::string queryTemplate;
    std::vector<std::string> params;
    
    if (excludeCloudScanned)
    {
        // Exclude cloud-scanned files (record_config = RECORD_CONFIG_CLOUD_SCANNED)
        // Note: LIMIT requires an integer, not a quoted string, so we append it directly
        queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
                     VideoRecordDBColumns::file_protection + " = CAST(" + PARAM_PLACEHOLDER(0) + " AS text) AND " +
                     VideoRecordDBColumns::record_config + " != " + PARAM_PLACEHOLDER(1) + " ORDER BY " + 
                     VideoRecordDBColumns::start_time + " ASC LIMIT " + to_string(batchSize) + ";";
        params = {"0", RECORD_CONFIG_CLOUD_SCANNED};
    }
    else
    {
        // Default: return all unprotected files
        // Note: LIMIT requires an integer, not a quoted string, so we append it directly
        queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
                     VideoRecordDBColumns::file_protection + " = CAST(" + PARAM_PLACEHOLDER(0) + " AS text) ORDER BY " + 
                     VideoRecordDBColumns::start_time + " ASC LIMIT " + to_string(batchSize) + ";";
        params = {"0"};
    }
    
    LOG(verbose) << "SQL query template: " << queryTemplate << " (excludeCloudScanned: " << excludeCloudScanned << ")" << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

std::vector<VideoRecordDBColumns> Postgresql::getVideoRecordFilePaths(string streamId, int64_t startTime, int64_t endTime)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;

    std::string queryTemplate = "SELECT " + VideoRecordDBColumns::file_path + "," +
                 VideoRecordDBColumns::duration + "," +
                 VideoRecordDBColumns::start_time + "," +
                 VideoRecordDBColumns::object_id + "," +
                 VideoRecordDBColumns::storage_location + "," +
                 VideoRecordDBColumns::record_config +
                 " FROM " + VideoRecordDBColumns::table_name +
                 " WHERE " + VideoRecordDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + "" +
                 " AND (" + VideoRecordDBColumns::start_time + " + " +
                 VideoRecordDBColumns::duration + " > " + PARAM_PLACEHOLDER(1) + "" +
                 " OR " + VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + ")" +
                 " AND " + VideoRecordDBColumns::start_time + " < " + PARAM_PLACEHOLDER(2) + "" +
                 " ORDER BY " + VideoRecordDBColumns::start_time + ";";
    std::vector<std::string> params = {streamId, std::to_string(startTime), std::to_string(endTime)};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, VideoRecordDBColumns::file_path))
            {
                row.filepath_value = column.second;
            }
            else if (iequals(column.first, VideoRecordDBColumns::duration))
            {
                row.duration_value = stringToLong(column.second);
            }
            else if (iequals(column.first, VideoRecordDBColumns::start_time))
            {
                row.start_time_value = stringToLong(column.second);
            }
            else if (iequals(column.first, VideoRecordDBColumns::object_id))
            {
                row.object_id_value = column.second;
            }
        }
        rows.push_back(row);
    }
    return rows;
}

std::vector<VideoRecordDBColumns> Postgresql::getVideoRecordFilePathsSensorIdBased(string sensorId, int64_t startTime, int64_t endTime)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;

    std::string queryTemplate = "SELECT " + VideoRecordDBColumns::file_path + "," +
                    VideoRecordDBColumns::duration + "," +
                    VideoRecordDBColumns::start_time + "," +
                    VideoRecordDBColumns::object_id + "," +
                    DBColumns::sensor_id + "," +
                    VideoRecordDBColumns::metadata_file_path + "," +
                    VideoRecordDBColumns::metadata_json + "," +
                    VideoRecordDBColumns::storage_location + "," +
                    VideoRecordDBColumns::record_config +
                    " FROM " + VideoRecordDBColumns::table_name +
                    " WHERE " + VideoRecordDBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + "" +
                    " AND (" + VideoRecordDBColumns::start_time + " + " +
                    VideoRecordDBColumns::duration + " > " + PARAM_PLACEHOLDER(1) + "" +
                    " OR " + VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + ")" +
                    " AND " + VideoRecordDBColumns::start_time + " < " + PARAM_PLACEHOLDER(2) + "" +
                    " ORDER BY " + VideoRecordDBColumns::start_time + ";";
    std::vector<std::string> params = {sensorId, std::to_string(startTime), std::to_string(endTime)};
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

std::vector<VideoRecordDBColumns> Postgresql::getVideoRecordFilePathsIdBased(string id)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;

    std::string queryTemplate = "SELECT " + VideoRecordDBColumns::file_path + "," +
                VideoRecordDBColumns::duration + "," +
                VideoRecordDBColumns::start_time + "," +
                VideoRecordDBColumns::object_id + "," +
                VideoRecordDBColumns::metadata_file_path + "," +
                VideoRecordDBColumns::metadata_json + "," +
                VideoRecordDBColumns::storage_location + "," +
                VideoRecordDBColumns::record_config +
                " FROM " + VideoRecordDBColumns::table_name +
                " WHERE " + VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {id};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

std::vector<VideoRecordDBColumns> Postgresql::getAllVideoRecordFilePaths()
{
    std::vector<VideoRecordDBColumns> rows;
    VideoRecordDBColumns row;
    queryResult result;
    std::string queryTemplate = "SELECT " + VideoRecordDBColumns::file_path + "," +
                 VideoRecordDBColumns::duration + "," +
                 VideoRecordDBColumns::start_time + "," +
                 VideoRecordDBColumns::object_id + "," +
                 VideoRecordDBColumns::stream_id + "," +
                 DBColumns::sensor_id + "," +
                 VideoRecordDBColumns::metadata_file_path + "," +
                 VideoRecordDBColumns::metadata_json + "," +
                 VideoRecordDBColumns::storage_location + "," +
                 VideoRecordDBColumns::record_config +
                 " FROM " + VideoRecordDBColumns::table_name + ";";
    LOG(verbose) << "SQL query template: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

int Postgresql::deleteStreamDetailsUsingSensorId(string sensorId)
{
    string id = sensorId;
    
    std::string queryTemplate = "DELETE FROM " + SensorStreamsDBColumns::table_name +
                 " WHERE " + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {id};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;  // Return 0 on success
}

int Postgresql::deleteRowStream(string streamId)
{    
    std::string queryTemplate = "DELETE FROM " + SensorStreamsDBColumns::table_name +
                 " WHERE " + SensorStreamsDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {streamId};
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;  // Return 0 on success
}

int Postgresql::deleteRecordingStatusUsingSensorId(string sensorId)
{
    std::string queryTemplate = "DELETE FROM " + RecordingStatusDBColumns::table_name +
                 " WHERE " + RecordingStatusDBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {sensorId};
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;  // Return 0 on success
}

int Postgresql::insertRowStream(SensorStreamsDBColumns &row)
{
    string currentUtcTime = getCurrentUtcTime();
    SensorStreamsDBColumns r = readSensorStreams(row.stream_id_value);
    row.live_url_value = row.live_url_value.empty() ? r.live_url_value : row.live_url_value;
    row.replay_url_value = row.replay_url_value.empty() ? r.replay_url_value : row.replay_url_value;
    row.proxy_url_value = row.proxy_url_value.empty() ? r.proxy_url_value : row.proxy_url_value;
    row.resolution_value = row.resolution_value.empty() ? r.resolution_value : row.resolution_value;
    row.frameRate_value = row.frameRate_value.empty() ? r.frameRate_value : row.frameRate_value;
    row.encoding_value = row.encoding_value.empty() ? r.encoding_value : row.encoding_value;
    row.streamStatus_value = row.streamStatus_value == -1 ? r.streamStatus_value : row.streamStatus_value;
    row.streamType_value = row.streamType_value == -1 ? r.streamType_value : row.streamType_value;
    row.encodingProfile_value = row.encodingProfile_value.empty() ? r.encodingProfile_value : row.encodingProfile_value;
    row.encodingInterval_value = row.encodingInterval_value.empty() ? r.encodingInterval_value : row.encodingInterval_value;
    row.duration_value = row.duration_value.empty() ? r.duration_value : row.duration_value;
    row.isMainStream_value = row.isMainStream_value.empty() ? r.isMainStream_value : row.isMainStream_value;
    row.isAlwaysRecording_value = row.isAlwaysRecording_value.empty() ? r.isAlwaysRecording_value : row.isAlwaysRecording_value;
    row.storageLocation_value = row.storageLocation_value == -1 ? r.storageLocation_value : row.storageLocation_value;
    row.bitrate_value = row.bitrate_value.empty() ? r.bitrate_value : row.bitrate_value;
    row.numFrames_value = row.numFrames_value.empty() ? r.numFrames_value : row.numFrames_value;
    row.audio_container_value = row.audio_container_value.empty() ? r.audio_container_value : row.audio_container_value;
    row.audio_encoding_value = row.audio_encoding_value.empty() ? r.audio_encoding_value : row.audio_encoding_value;
    row.audio_sample_rate_value = row.audio_sample_rate_value.empty() ? r.audio_sample_rate_value : row.audio_sample_rate_value;
    row.audio_bps_value = row.audio_bps_value.empty() ? r.audio_bps_value : row.audio_bps_value;
    row.audio_channels_value = row.audio_channels_value.empty() ? r.audio_channels_value : row.audio_channels_value;
    row.streamName_value = row.streamName_value.empty() ? r.streamName_value : row.streamName_value;
    // Special handling for isBframesPresent: preserve database value 1 (true) when incoming is 0 (false)
    if (r.isBframesPresent_value == 1 && row.isBframesPresent_value == 0)
    {
        row.isBframesPresent_value = r.isBframesPresent_value;
    }

    row.created_date_time_value = r.created_date_time_value.empty() ? currentUtcTime : r.created_date_time_value;
    row.modified_date_time_value = currentUtcTime;

    /* Create SQL statement using parameterized query to prevent SQL injection */
    std::string queryTemplate = "INSERT INTO " + SensorStreamsDBColumns::table_name + "(" +
                DBColumns::sensor_id + "," +
                SensorStreamsDBColumns::live_url + "," +
                SensorStreamsDBColumns::replay_url + "," +
                SensorStreamsDBColumns::proxy_url + "," +
                SensorStreamsDBColumns::resolution + "," +
                SensorStreamsDBColumns::frameRate + "," +
                SensorStreamsDBColumns::encoding + "," +
                SensorStreamsDBColumns::stream_id + "," +
                SensorStreamsDBColumns::streamStatus + "," +
                SensorStreamsDBColumns::type + "," +
                SensorStreamsDBColumns::encodingProfile + "," +
                SensorStreamsDBColumns::encodingInterval + "," +
                SensorStreamsDBColumns::duration + "," +
                SensorStreamsDBColumns::isMainStream + "," +
                SensorStreamsDBColumns::isAlwaysRecording + "," +
                SensorStreamsDBColumns::storageLocation + "," +
                SensorStreamsDBColumns::bitrate + "," +
                SensorStreamsDBColumns::numFrames + "," +
                SensorStreamsDBColumns::audio_container + "," +
                SensorStreamsDBColumns::audio_encoding + "," +
                SensorStreamsDBColumns::audio_sample_rate + "," +
                SensorStreamsDBColumns::audio_bps + "," +
                SensorStreamsDBColumns::audio_channels + "," +
                SensorStreamsDBColumns::streamName + "," +
                SensorStreamsDBColumns::isBframesPresent + "," +
                SensorStreamsDBColumns::created_date_time + "," +
                SensorStreamsDBColumns::modified_date_time;

                std::vector<std::string> params = {
                    row.sensor_id_value, row.live_url_value, row.replay_url_value, row.proxy_url_value,
                    row.resolution_value, row.frameRate_value, row.encoding_value, row.stream_id_value,
                    std::to_string(row.streamStatus_value), std::to_string(row.streamType_value),
                    row.encodingProfile_value, row.encodingInterval_value, row.duration_value,
                    row.isMainStream_value, row.isAlwaysRecording_value, std::to_string(row.storageLocation_value), row.bitrate_value,
                    row.numFrames_value, row.audio_container_value, row.audio_encoding_value,
                    row.audio_sample_rate_value, row.audio_bps_value, row.audio_channels_value,
                    row.streamName_value, std::to_string(row.isBframesPresent_value), row.created_date_time_value, row.modified_date_time_value
                };

                // Use BUILD_VALUES_CLAUSE to add the VALUES clause with parameter placeholders
                BUILD_VALUES_CLAUSE(queryTemplate, params);

                // Add the ON CONFLICT clause
                queryTemplate += " ON CONFLICT (" + SensorStreamsDBColumns::stream_id + ") DO UPDATE SET " +
                 SensorStreamsDBColumns::live_url + " = EXCLUDED." + SensorStreamsDBColumns::live_url + ", " +
                 SensorStreamsDBColumns::replay_url + " = EXCLUDED." + SensorStreamsDBColumns::replay_url+ ", " +
                 SensorStreamsDBColumns::proxy_url + " = EXCLUDED." + SensorStreamsDBColumns::proxy_url + ", " +
                 SensorStreamsDBColumns::resolution + " = EXCLUDED." + SensorStreamsDBColumns::resolution + ", " +
                 SensorStreamsDBColumns::frameRate + " = EXCLUDED." + SensorStreamsDBColumns::frameRate + ", " +
                 SensorStreamsDBColumns::encoding + " = EXCLUDED." + SensorStreamsDBColumns::encoding + ", " +
                 SensorStreamsDBColumns::stream_id + " = EXCLUDED." + SensorStreamsDBColumns::stream_id + ", " +
                 SensorStreamsDBColumns::streamStatus + " = EXCLUDED." + SensorStreamsDBColumns::streamStatus + ", " +
                 SensorStreamsDBColumns::type + " = EXCLUDED." + SensorStreamsDBColumns::type + ", " +
                 SensorStreamsDBColumns::encodingProfile + " = EXCLUDED." + SensorStreamsDBColumns::encodingProfile + ", " +
                 SensorStreamsDBColumns::encodingInterval + " = EXCLUDED." + SensorStreamsDBColumns::encodingInterval + ", " +
                 SensorStreamsDBColumns::duration + " = EXCLUDED." + SensorStreamsDBColumns::duration + ", " +
                 SensorStreamsDBColumns::isMainStream + " = EXCLUDED." + SensorStreamsDBColumns::isMainStream + ", " +
                 SensorStreamsDBColumns::isAlwaysRecording + " = EXCLUDED." + SensorStreamsDBColumns::isAlwaysRecording + ", " +
                 SensorStreamsDBColumns::storageLocation + " = EXCLUDED." + SensorStreamsDBColumns::storageLocation + ", " +
                 SensorStreamsDBColumns::bitrate + " = EXCLUDED." + SensorStreamsDBColumns::bitrate + ", " +
                 SensorStreamsDBColumns::numFrames + " = EXCLUDED." + SensorStreamsDBColumns::numFrames + ", " +
                 SensorStreamsDBColumns::audio_container + " = EXCLUDED." + SensorStreamsDBColumns::audio_container + ", " +
                 SensorStreamsDBColumns::audio_encoding + " = EXCLUDED." + SensorStreamsDBColumns::audio_encoding + ", " +
                 SensorStreamsDBColumns::audio_sample_rate + " = EXCLUDED." + SensorStreamsDBColumns::audio_sample_rate + ", " +
                 SensorStreamsDBColumns::audio_bps + " = EXCLUDED." + SensorStreamsDBColumns::audio_bps + ", " +
                 SensorStreamsDBColumns::audio_channels + " = EXCLUDED." + SensorStreamsDBColumns::audio_channels + ", " +
                 SensorStreamsDBColumns::streamName + " = EXCLUDED." + SensorStreamsDBColumns::streamName + ", " +
                 SensorStreamsDBColumns::isBframesPresent + " = EXCLUDED." + SensorStreamsDBColumns::isBframesPresent + ", " +
                 SensorStreamsDBColumns::created_date_time + " = EXCLUDED." + SensorStreamsDBColumns::created_date_time + ", " +
                 SensorStreamsDBColumns::modified_date_time + " = EXCLUDED." + SensorStreamsDBColumns::modified_date_time + ";";

    LOG(verbose) << "SQL query template: " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Postgresql::updateStreamInfo(string streamId, string proxyUrl, string replayUrl, std::pair<StreamStatus, string> status)
{
    string currentUtcTime = getCurrentUtcTime();
    
    std::string queryTemplate = "UPDATE " + SensorStreamsDBColumns::table_name +
                " SET " + SensorStreamsDBColumns::proxy_url + " = " + PARAM_PLACEHOLDER(0) + ", " +
                SensorStreamsDBColumns::replay_url + " = " + PARAM_PLACEHOLDER(1) + ", " +
                SensorStreamsDBColumns::streamStatus + " = " + PARAM_PLACEHOLDER(2) + ", " +
                SensorStreamsDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(3) + "" +
                " WHERE " + SensorStreamsDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(4) + ";";
    std::vector<std::string> params = {proxyUrl, replayUrl, std::to_string(status.first), currentUtcTime, streamId};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    { 
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }

    return 0;
}

SensorStreamsDBColumns Postgresql::readSensorStreams(string streamId)
{
    SensorStreamsDBColumns row;

    queryResult result;
    
    std::string queryTemplate = "SELECT * FROM " + SensorStreamsDBColumns::table_name +
                 " WHERE " + SensorStreamsDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {streamId};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        sensorStreamHelper(row, entries);
    }
    return row;
}

vector<SensorStreamsDBColumns> Postgresql::readAllStreams()
{
    vector<SensorStreamsDBColumns> rows;

    queryResult result;
    std::string queryTemplate = "SELECT * FROM " + SensorStreamsDBColumns::table_name + ";";
    LOG(verbose) << "SQL query template: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        SensorStreamsDBColumns row;
        sensorStreamHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

vector<SensorStreamsDBColumns> Postgresql::readAllStreamsForGivenSensorID(string sensorId)
{
    vector<SensorStreamsDBColumns> rows;

    queryResult result;
    
    std::string queryTemplate = "SELECT * FROM " + SensorStreamsDBColumns::table_name +
                 " WHERE " + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {sensorId};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        SensorStreamsDBColumns row;
        sensorStreamHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

vector<SensorInfoDBColumns> Postgresql::readSensorInfo(string sensorId)
{
    vector<SensorInfoDBColumns> rows;
    queryResult result;

    std::string queryTemplate = "SELECT * FROM " + SensorDetailsDBColumns::table_name +
                 " INNER JOIN " + SensorStreamsDBColumns::table_name +
                 " USING (" + DBColumns::sensor_id + ") WHERE " +
                 SensorStreamsDBColumns::table_name + "." + DBColumns::sensor_id +
                 " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {sensorId};
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        SensorInfoDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, SensorInfoDBColumns::device_id))
            {
                row.device_id_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::sensor_id))
            {
                row.sensor_id_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::sensor_hw_id))
            {
                row.sensor_hw_id_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::username))
            {
                row.username_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::password))
            {
                std::string encryptedPassword = column.second;
                std::string decryptedPassword = EMPTY_STRING;
                if (!encryptedPassword.empty() &&
                    vst_common::decrypt_data(encryptedPassword, decryptedPassword, row.sensor_id_value) == false)
                {
                    LOG(error) << "failed to decrypt" << endl;
                }
                row.password_value = decryptedPassword;
                LOG(error) << "password_value: " << maskSensitiveData(row.password_value, MaskType::PASSWORD) << endl;
            }
            else if (iequals(column.first, SensorInfoDBColumns::name))
            {
                row.name_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::ip))
            {
                row.ip_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::hardware))
            {
                row.hardware_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::manufacturer))
            {
                row.manufacturer_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::serial_number))
            {
                row.serial_number_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::firmware_version))
            {
                row.firmware_version_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::hardware_id))
            {
                row.hardware_id_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::location))
            {
                row.location_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::tags))
            {
                row.tags_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::url))
            {
                row.url_value = column.second;
            }
            else if (iequals(column.first, SensorDetailsDBColumns::type))
            {
                row.type_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::position))
            {
                row.position_value = column.second;
                /**
                 *  NOTE: Not required for postgresql
                 *   if (row.position_value.size() >= 2)
                 *   {
                 *       // remove quotation from JSON string
                 *       row.position_value = row.position_value.substr(1, row.position_value.size() - 2);
                 *   }
                 */
            }
            else if (iequals(column.first, SensorInfoDBColumns::users))
            {
                row.users_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::isRemoteSensor))
            {
                row.isRemoteSensor_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::remoteDeviceId))
            {
                row.remoteDeviceId_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::remoteDeviceName))
            {
                row.remoteDeviceName_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::remoteDeviceLocation))
            {
                row.remoteDeviceLocation_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::httpStatus))
            {
                row.httpStatus_value = stringToLong(column.second);
            }
            else if (iequals(column.first, SensorInfoDBColumns::sensorStatus))
            {
                row.sensorStatus_value = stringToLong(column.second);
            }
            else if (iequals(column.first, SensorInfoDBColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::stream_id))
            {
                row.stream_id_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::live_url))
            {
                row.live_url_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::replay_url))
            {
                row.replay_url_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::proxy_url))
            {
                row.proxy_url_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::resolution))
            {
                row.resolution_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::frameRate))
            {
                row.frameRate_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::encoding))
            {
                row.encoding_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::streamStatus))
            {
                row.streamStatus_value = stringToLong(column.second);
            }
            else if (iequals(column.first, SensorStreamsDBColumns::type))
            {
                row.streamType_value = stringToLong(column.second);
            }
            else if (iequals(column.first, SensorInfoDBColumns::encodingProfile))
            {
                row.encodingProfile_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::encodingInterval))
            {
                row.encodingInterval_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::duration))
            {
                row.duration_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::isMainStream))
            {
                row.isMainStream_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::isAlwaysRecording))
            {
                row.isAlwaysRecording_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::bitrate))
            {
                row.bitrate_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::numFrames))
            {
                row.numFrames_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::audio_container))
            {
                row.audio_container_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::audio_encoding))
            {
                row.audio_encoding_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::audio_sample_rate))
            {
                row.audio_sample_rate_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::audio_bps))
            {
                row.audio_bps_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::audio_channels))
            {
                row.audio_channels_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
            else if (iequals(column.first, SensorStreamsDBColumns::isBframesPresent))
            {
                row.isBframesPresent_value = stringToInt(column.second, 0);
            }
        }
        rows.push_back(row);
    }
    return rows;
}

string Postgresql::readStreamProperty(string streamId, string property)
{
    string value = "";

    queryResult result;

    // Validate property name against whitelist to prevent SQL injection
    std::string safeProperty = validateSensorStreamTableProperty(property);
    if (safeProperty.empty())
    {
        LOG(error) << "Invalid property name: " << property << endl;
        return value;
    }

    std::string queryTemplate = "SELECT " + safeProperty + " FROM " + SensorStreamsDBColumns::table_name +
                 " WHERE " + SensorStreamsDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {streamId};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return value;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, property))
            {
                value = column.second;
            }
        }
    }
    return value;
}

/* Search Sensor ID based on Object ID */
string Postgresql::searchSensorFileIdBased(const string &id)
{
    string sensorId = "";
    queryResult result;
    
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT " + VideoRecordDBColumns::sensor_id + " FROM " + VideoRecordDBColumns::table_name +
                          " WHERE " + VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {id};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return sensorId;
    }
    
    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, VideoRecordDBColumns::sensor_id))
            {
                sensorId = column.second;
            }
        }
    }
    return sensorId;
}

std::vector<VideoRecordDBColumns> Postgresql::getRecordedVideoSize()
{
    // get the file size row from the Video records table
    vector<VideoRecordDBColumns> rows;

    queryResult result;
    std::string queryTemplate = "SELECT " + VideoRecordDBColumns::file_size + " FROM " + VideoRecordDBColumns::table_name +
                 " WHERE " + VideoRecordDBColumns::file_size + " IS NOT NULL;";
    LOG(verbose) << "SQL query template: " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }
    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, VideoRecordDBColumns::file_size))
            {
                row.filesize_value = stringToLong(column.second);
            }
        }
        rows.push_back(row);
    }
    return rows;
}

UserDetailsDBColumns Postgresql::getUserDetail(const string username)
{
    UserDetailsDBColumns row;
    queryResult result;

    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + UserDetailsDBColumns::table_name +
                          " WHERE " + UserDetailsDBColumns::username + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {username};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, UserDetailsDBColumns::username))
            {
                row.username_value = column.second;
            }
            else if (iequals(column.first, UserDetailsDBColumns::password_hash))
            {
                row.password_hash_value = column.second;
            }
            else if (iequals(column.first, UserDetailsDBColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, UserDetailsDBColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
        }
    }
    return row;
}

int Postgresql::setUserDetail(UserDetailsDBColumns &row)
{
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement */
    /* Create SQL statement with parameterized query using safe macros */
    std::string queryTemplate = "INSERT INTO " + UserDetailsDBColumns::table_name + "(";
    
    APPEND_COLUMN(UserDetailsDBColumns::username, row.username_value, queryTemplate)
    APPEND_COLUMN(UserDetailsDBColumns::password_hash, row.password_hash_value, queryTemplate)
    APPEND_COLUMN(UserDetailsDBColumns::created_date_time, currentUtcTime, queryTemplate)
    APPEND_COLUMN(UserDetailsDBColumns::modified_date_time, currentUtcTime, queryTemplate)
    queryTemplate.pop_back(); // Remove trailing comma
    
    // Build parameterized values using safe macros
    std::vector<std::string> params;
    APPEND_COLUMN_VALUE(row.username_value, params)
    APPEND_COLUMN_VALUE(row.password_hash_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(queryTemplate, params);
    queryTemplate += " ON CONFLICT DO NOTHING;";

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Postgresql::deleteUserDetails(const string username)
{

    if (username == DEFAULT_ADMIN_USERNAME)
    {
        LOG(error) << "Cannot delete admin user" << endl;
        return -1;
    }
    
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "DELETE FROM " + UserDetailsDBColumns::table_name +
                          " WHERE " + UserDetailsDBColumns::username + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {username};
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

std::vector<UserSessionsDBColumns> Postgresql::getUserSessions(const string username)
{
    deleteExpiredUserSessions();
    vector<UserSessionsDBColumns> rows;
    queryResult result;

    std::string queryTemplate = "SELECT * FROM " + UserSessionsDBColumns::table_name +
                 " WHERE " + UserSessionsDBColumns::username + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {username};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        UserSessionsDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, UserSessionsDBColumns::username))
            {
                row.username_value = column.second;
            }
            else if (iequals(column.first, UserSessionsDBColumns::session_cookie))
            {
                row.session_cookie_value = column.second;
            }
            else if (iequals(column.first, UserSessionsDBColumns::cookie_max_age))
            {
                row.cookie_max_age_value = stringToLong(column.second);
            }
            else if (iequals(column.first, UserSessionsDBColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, UserSessionsDBColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
        }
        rows.push_back(row);
    }
    return rows;
}

int Postgresql::deleteUserSession(const string username, const string sessionId)
{

    /* Create SQL statement with parameterized query using safe macros */
    std::string queryTemplate = "DELETE FROM " + UserSessionsDBColumns::table_name +
                 " WHERE " + UserSessionsDBColumns::username + " = " + PARAM_PLACEHOLDER(0) + " AND " +
                 UserSessionsDBColumns::session_cookie + " = " + PARAM_PLACEHOLDER(1) + ";";
    
    // Build parameterized values using safe macros
    std::vector<std::string> params;
    APPEND_COLUMN_VALUE(username, params)
    APPEND_COLUMN_VALUE(sessionId, params)
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Postgresql::setUserSession(UserSessionsDBColumns &row)
{

    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement with parameterized query using safe macros */
    std::string queryTemplate = "INSERT INTO " + UserSessionsDBColumns::table_name + "(";
    
    APPEND_COLUMN(UserSessionsDBColumns::username, row.username_value, queryTemplate)
    APPEND_COLUMN(UserSessionsDBColumns::session_cookie, row.session_cookie_value, queryTemplate)
    APPEND_COLUMN(UserSessionsDBColumns::cookie_max_age, std::to_string(row.cookie_max_age_value), queryTemplate)
    APPEND_COLUMN(UserSessionsDBColumns::created_date_time, currentUtcTime, queryTemplate)
    APPEND_COLUMN(UserSessionsDBColumns::modified_date_time, currentUtcTime, queryTemplate)
    queryTemplate.pop_back(); // Remove trailing comma
    
    // Build parameterized values using safe macros
    std::vector<std::string> params;
    APPEND_COLUMN_VALUE(row.username_value, params)
    APPEND_COLUMN_VALUE(row.session_cookie_value, params)
    APPEND_COLUMN_VALUE_INT(row.cookie_max_age_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(queryTemplate, params);
    queryTemplate += ";";

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }

    return 0;
}

void Postgresql::deleteExpiredUserSessions()
{
    int64_t currentUnixTimestamp = getCurrentUnixTimestamp();
    std::string queryTemplate = "DELETE FROM " + UserSessionsDBColumns::table_name +
                 " WHERE " + UserSessionsDBColumns::cookie_max_age + " <= " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {std::to_string(currentUnixTimestamp)};
    LOG(verbose) << "SQL query template: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }
    return;
}

void Postgresql::extendSession(const string username, const string sessionId)
{
    string currentUtcTime = getCurrentUtcTime();
    int64_t newMaxAge = getCurrentUnixTimestamp() + GET_CONFIG().session_max_age_sec;
    
    std::string queryTemplate = "UPDATE " + UserSessionsDBColumns::table_name +
                 " SET " + UserSessionsDBColumns::cookie_max_age + " = " + PARAM_PLACEHOLDER(0) + "" +
                 " , " + UserSessionsDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + "" +
                 " WHERE " + UserSessionsDBColumns::username + " = " + PARAM_PLACEHOLDER(2) + " AND " +
                 UserSessionsDBColumns::session_cookie + " = " + PARAM_PLACEHOLDER(3) + ";";
    std::vector<std::string> params = {std::to_string(newMaxAge), currentUtcTime, username, sessionId};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }
    return;
}

std::vector<UserSessionsDBColumns> Postgresql::getAllSessions()
{
    deleteExpiredUserSessions();
    vector<UserSessionsDBColumns> rows;
    queryResult result;

    std::string queryTemplate = "SELECT * FROM " + UserSessionsDBColumns::table_name + ";";
    LOG(verbose) << "SQL query template: " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        UserSessionsDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, UserSessionsDBColumns::username))
            {
                row.username_value = column.second;
            }
            else if (iequals(column.first, UserSessionsDBColumns::session_cookie))
            {
                row.session_cookie_value = column.second;
            }
            else if (iequals(column.first, UserSessionsDBColumns::cookie_max_age))
            {
                row.cookie_max_age_value = stringToLong(column.second);
            }
            else if (iequals(column.first, UserSessionsDBColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, UserSessionsDBColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
        }
        rows.push_back(row);
    }
    return rows;
}

bool Postgresql::checkVideoRecordExists(string streamId)
{
    bool ret = false;
    VideoRecordDBColumns row;
    queryResult result;

    std::string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name +
                 " WHERE " + VideoRecordDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {streamId};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return false;
    }
    if (result.size() > 0)
    {
        ret = true;
    }
    return ret;
}

std::vector<VideoRecordDBColumns> Postgresql::getAllDisconnectedSensorId()
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;

    std::string queryTemplate = "SELECT DISTINCT " + DBColumns::sensor_id + ", " + VideoRecordDBColumns::sensor_name +
                 " FROM " + VideoRecordDBColumns::table_name +
                 " WHERE " + DBColumns::sensor_id +
                 " NOT IN (SELECT DISTINCT " + DBColumns::sensor_id +
                 " FROM " + SensorDetailsDBColumns::table_name + ");";
    LOG(verbose) << "SQL query template: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, VideoRecordDBColumns::sensor_id))
            {
                row.sensor_id_value = column.second;
            }
            else if (iequals(column.first, VideoRecordDBColumns::sensor_name))
            {
                row.sensor_name_value = column.second;
            }
        }
        rows.push_back(row);
    }
    return rows;
}

std::string Postgresql::getLocalDeviceId()
{
    LocalDeviceDetailsDBColumns row;
    queryResult result;
    std::string queryTemplate = "SELECT " + LocalDeviceDetailsDBColumns::id + " FROM " + LocalDeviceDetailsDBColumns::table_name + ";";
    LOG(verbose) << "SQL query template: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row.id_value;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, LocalDeviceDetailsDBColumns::id))
            {
                row.id_value = column.second;
            }
        }
    }

    return row.id_value;
}

std::string Postgresql::getLocalDeviceName()
{
    LocalDeviceDetailsDBColumns row;
    queryResult result;
    std::string queryTemplate = "SELECT " + LocalDeviceDetailsDBColumns::name + " FROM " + LocalDeviceDetailsDBColumns::table_name + ";";
    LOG(verbose) << "SQL query template: " << queryTemplate << endl;
    if (!executeQuery(queryTemplate, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row.name_value;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, LocalDeviceDetailsDBColumns::name))
            {
                row.name_value = column.second;
            }
        }
    }

    return row.name_value;
}

VmsErrorCode Postgresql::getRecordingStatus(std::map<std::string, RecordingStatusDBColumns, std::less<>> &allStatus, const std::optional<string> &streamId)
{
    queryResult result;
    std::string queryTemplate;
    std::vector<std::string> params;
    
    if (streamId.has_value())
    {
        queryTemplate = "SELECT  *  FROM " +
              RecordingStatusDBColumns::table_name + " WHERE " + RecordingStatusDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ";";
        params = {*streamId};
    }
    else
    {
        queryTemplate = "SELECT  *  FROM " +
              RecordingStatusDBColumns::table_name + ";";
    }

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return VmsErrorCode::VMSInternalError;
    }

    for (auto entries : result)
    {
        RecordingStatusDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, RecordingStatusDBColumns::sensor_id))
            {
                row.sensor_id_value = column.second;
            }
            else if (iequals(column.first, RecordingStatusDBColumns::stream_id))
            {
                row.stream_id_value = column.second;
            }
            else if (iequals(column.first, RecordingStatusDBColumns::recordingStatus))
            {
                row.recordingStatus_value = stoi(column.second);
            }
            else if (iequals(column.first, RecordingStatusDBColumns::created_date_time))
            {
                row.created_date_time_value = stoi(column.second);
            }
            else if (iequals(column.first, RecordingStatusDBColumns::modified_date_time))
            {
                row.modified_date_time_value = stoi(column.second);
            }
        }
        allStatus[row.stream_id_value] = row;
    }
    return VmsErrorCode::NoError;
}

int Postgresql::setLocalDeviceId(const string deviceId)
{
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement with parameterized query using safe macros */
    std::string queryTemplate = "INSERT INTO " + LocalDeviceDetailsDBColumns::table_name + "(";
    
    APPEND_COLUMN(LocalDeviceDetailsDBColumns::id, deviceId, queryTemplate)
    APPEND_COLUMN(DBColumns::created_date_time, currentUtcTime, queryTemplate)
    APPEND_COLUMN(DBColumns::modified_date_time, currentUtcTime, queryTemplate)
    queryTemplate.pop_back(); // Remove trailing comma
    
    // Build parameterized values using safe macros
    std::vector<std::string> params;
    APPEND_COLUMN_VALUE(deviceId, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(queryTemplate, params);
    queryTemplate += ";";

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Postgresql::setLocalDeviceName(const string &deviceName, const string &deviceId)
{
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement */
    std::string queryTemplate = "UPDATE " + LocalDeviceDetailsDBColumns::table_name +
                 " SET " + LocalDeviceDetailsDBColumns::name + " = " + PARAM_PLACEHOLDER(0) + ", " +
                 LocalDeviceDetailsDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + "" +
                 " WHERE " + LocalDeviceDetailsDBColumns::id + " = " + PARAM_PLACEHOLDER(2) + ";";
    std::vector<std::string> params = {deviceName, currentUtcTime, deviceId};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Postgresql::setRecordingStatus(const std::string &streamId, RecordState new_status, const std::optional<string> &sensorId)
{
    queryResult result;
    std::string queryTemplate;
    std::vector<std::string> params;
    string currentUtcTime = getCurrentUtcTime();

    if (sensorId.has_value())
    {
        // If sensorId is provided, use INSERT INTO with ON CONFLICT for the given sensorId
        queryTemplate = "INSERT INTO " + RecordingStatusDBColumns::table_name + " (";
        
        APPEND_COLUMN(RecordingStatusDBColumns::stream_id, streamId, queryTemplate)
        APPEND_COLUMN_OPTIONAL(RecordingStatusDBColumns::sensor_id, sensorId, queryTemplate)
        APPEND_COLUMN(RecordingStatusDBColumns::recordingStatus, std::to_string(static_cast<int>(new_status)), queryTemplate)
        APPEND_COLUMN(RecordingStatusDBColumns::created_date_time, currentUtcTime, queryTemplate)
        APPEND_COLUMN(RecordingStatusDBColumns::modified_date_time, currentUtcTime, queryTemplate)
        queryTemplate.pop_back(); // Remove trailing comma
        
        // Build parameterized values using safe macros
        params.clear();
        APPEND_COLUMN_VALUE(streamId, params)
        APPEND_COLUMN_VALUE_OPTIONAL(sensorId, params)
        APPEND_COLUMN_VALUE_INT(static_cast<int>(new_status), params)
        APPEND_COLUMN_VALUE(currentUtcTime, params)
        APPEND_COLUMN_VALUE(currentUtcTime, params)
        
        // Add VALUES clause with automatic placeholders using advanced macro
        BUILD_VALUES_CLAUSE(queryTemplate, params);
        
        // Add the additional parameter for the ON CONFLICT clause before building the query
        APPEND_COLUMN_VALUE(currentUtcTime, params)
        
        queryTemplate += " ON CONFLICT (" + RecordingStatusDBColumns::stream_id +
              ") DO UPDATE SET " +
              RecordingStatusDBColumns::sensor_id + " = EXCLUDED." + RecordingStatusDBColumns::sensor_id + ", " +
              RecordingStatusDBColumns::recordingStatus + " = EXCLUDED." + RecordingStatusDBColumns::recordingStatus + ", " +
              RecordingStatusDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(params.size() - 1) + ";";
    }
    else
    {
        // If sensorId is not provided, only update the recordingStatus for the given streamId
        queryTemplate = "UPDATE " + RecordingStatusDBColumns::table_name +
              " SET " + RecordingStatusDBColumns::recordingStatus + " = " + PARAM_PLACEHOLDER(0) + ", " +
              RecordingStatusDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + " " +
              "WHERE " + RecordingStatusDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(2) + ";";
        params = {std::to_string(static_cast<int>(new_status)), currentUtcTime, streamId};
    }

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }

    return 0;
}

std::string Postgresql::getLocalDeviceLocation()
{
    LocalDeviceDetailsDBColumns row;
    queryResult result;
    std::string queryTemplate = "SELECT " + LocalDeviceDetailsDBColumns::location + " FROM " + LocalDeviceDetailsDBColumns::table_name + ";";
    LOG(verbose) << "SQL query template: " << queryTemplate << endl;
    if (!executeQuery(queryTemplate, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row.location_value;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, LocalDeviceDetailsDBColumns::location))
            {
                row.location_value = column.second;
            }
        }
    }
    return row.location_value;
}

int Postgresql::setLocalDeviceLocation(const string &deviceLocation, const string &deviceId)
{
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement */
    std::string queryTemplate = "UPDATE " + LocalDeviceDetailsDBColumns::table_name +
                 " SET " + LocalDeviceDetailsDBColumns::location + " = " + PARAM_PLACEHOLDER(0) + ", " +
                 LocalDeviceDetailsDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + "" +
                 " WHERE " + LocalDeviceDetailsDBColumns::id + " = " + PARAM_PLACEHOLDER(2) + ";";
    std::vector<std::string> params = {deviceLocation, currentUtcTime, deviceId};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

/* File Type Sensor Usecase for ReplayStream */
std::vector<VideoFileInfo> Postgresql::getFileListStreamIdBased(std::string streamId, int64_t t1, int64_t t2)
{
    LOG(info) << "Postgresql::getFileListStreamIdBased stream id: " << streamId << " startTime: " << t1 << " endTime: " << t2 << endl;
    std::vector<VideoFileInfo> list;


    /* Assume t2 as max int if not specified
    ** so that all records will be fetched
    */
    if (t2 == 0)
    {
        t2 = std::numeric_limits<int64_t>::max();
    }

    VideoRecordDBColumns dbRow;
    std::vector<VideoRecordDBColumns> rows;
    rows = readVideoRecordStreamIdBased(streamId, t1, t2);

    for (size_t i = 0; i < rows.size(); i++)
    {
        VideoFileInfo file(rows[i]);

        {
            // Only normalize paths for LOCAL files, not cloud object keys
            if (rows[i].storage_location_value == StreamStorageTypeLocal)
            {
                // Normalize relative paths by prepending nv_streamer_directory_path
                string originalPath = file.m_filePath;
                file.m_filePath = normalizeRelativePath(file.m_filePath, GET_CONFIG().nv_streamer_directory_path);
                if (originalPath != file.m_filePath)
                {
                    LOG(info) << "Normalized relative path for LOCAL file: " << originalPath << " -> " << file.m_filePath << endl;
                }
                if (isFileExist(file.m_filePath) || !file.m_objectId.empty())
                {
                    list.push_back(file);
                    LOG(info) << "File path = " << file.m_filePath << endl;
                    LOG(info) << "File start time = " << file.m_startTime << endl;
                }
                else
                {
                    LOG(error) << "Local file " << file.m_filePath << " not present." << endl;
                }
            }
            else // StreamStorageTypeCloud
            {
                // For cloud files, keep the object key as-is (don't normalize)
                LOG(info) << "Cloud file - keeping object key as-is: " << file.m_filePath << endl;
                list.push_back(file);
            }
        }
    }

    return list;
}

/* File Type Sensor Usecase for Storage Service Download */
std::vector<VideoFileInfo> Postgresql::getFileListUniqueIdSensorIdBased(std::string uniqueId, std::string sensorId, int64_t t1, int64_t t2)
{
    LOG(info) << "Postgresql::getFileListUniqueIdSensorIdBased stream id: " << sensorId << " startTime: " << t1 << " endTime: " << t2 << endl;
    std::vector<VideoFileInfo> list;


    /* Assume t2 as max int if not specified
    ** so that all records will be fetched
    */
    if (t2 == 0)
    {
        t2 = std::numeric_limits<int64_t>::max();
    }

    VideoRecordDBColumns dbRow;
    std::vector<VideoRecordDBColumns> rows;
    if (uniqueId.empty() == false)
    {
        if (sensorId.empty() == false)
        {
            rows = readVideoRecordSensorIdUniqueIdBased(sensorId, uniqueId);
        }
        else
        {
            rows = readVideoRecordUniqueIdBased(uniqueId);
        }
    }
    else
    {
        rows = readVideoRecordSensorIdBased(sensorId, t1, t2);
    }

    for (size_t i = 0; i < rows.size(); i++)
    {
        VideoFileInfo file(rows[i]);

        {
            // Only normalize paths for LOCAL files, not cloud object keys
            if (rows[i].storage_location_value == StreamStorageTypeLocal)
            {
                // Normalize relative paths by prepending nv_streamer_directory_path
                string originalPath = file.m_filePath;
                file.m_filePath = normalizeRelativePath(file.m_filePath, GET_CONFIG().nv_streamer_directory_path);
                if (originalPath != file.m_filePath)
                {
                    LOG(info) << "Normalized relative path for LOCAL file: " << originalPath << " -> " << file.m_filePath << endl;
                }
                if (isFileExist(file.m_filePath) || !file.m_objectId.empty())
                {
                    list.push_back(file);
                    LOG(info) << "File path = " << file.m_filePath << endl;
                    LOG(info) << "File start time = " << file.m_startTime << endl;
                }
                else
                {
                    LOG(error) << "Local file " << file.m_filePath << " not present." << endl;
                }
            }
            else // StreamStorageTypeCloud
            {
                // For cloud files, keep the object key as-is (don't normalize)
                LOG(info) << "Cloud file - keeping object key as-is: " << file.m_filePath << endl;
                list.push_back(file);
                LOG(info) << "Cloud file path = " << file.m_filePath << endl;
                LOG(info) << "Cloud file start time = " << file.m_startTime << endl;
            }
        }
    }

    return list;
}

/* Normal VST Usecase for Storage Service Download and ReplayStream */
std::vector<VideoFileInfo> Postgresql::getFileList(std::string sensorId, int64_t t1,
                                                   int64_t t2, size_t maxDownloadSize, bool accurate /*false*/)
{
    LOG(info) << "Postgresql::getFileList sensor id: " << sensorId << " startTime: " << t1 << " endTime: " << t2 << endl;
    std::vector<VideoFileInfo> list;
    uint32_t max_size = 0;

    if (accurate && (t1 == 0 || t2 == 0))
    {
        LOG(error) << "Cannot enforce accurate flag when start or end is not provided" << endl;
        return list;
    }

    /* Assume t2 as max int if not specified
    ** so that all records will be fetched
    */
    if (t2 == 0)
    {
        t2 = std::numeric_limits<int64_t>::max();
    }

    VideoRecordDBColumns dbRow;
    std::vector<VideoRecordDBColumns> rows = readVideoRecord(sensorId, t1, t2);

    uint64_t maxFiles = 0;
    if (maxDownloadSize)
    {
        uint64_t totalRecordSize = 0;
        uint64_t maxDownloadSize_bytes = to_bytes(maxDownloadSize);
        for (maxFiles = 0; maxFiles < rows.size(); ++maxFiles)
        {
            totalRecordSize += rows[maxFiles].filesize_value;
            if (totalRecordSize > maxDownloadSize_bytes)
            {
                if (maxFiles)
                {
                    --maxFiles;
                }
                break;
            }
        }
    }

    max_size = rows.size();
    if (maxFiles)
    {
        max_size = std::min(rows.size(), maxFiles);
        if (max_size != rows.size())
        {
            LOG(warning) << "Max download limit exceeded, serving only " << max_size << " files of data" << endl;
        }
    }

    // Preform checks if accurate flag is set
    if (accurate && rows.size() != 0)
    {
        // check if last file being written
        LOG(info) << "Check last file being written" << endl;
        if (rows[max_size - 1].duration_value == FILE_INIT_DURATION)
        {
            LOG(error) << "Last file not ready" << endl;
            return list;
        }

        // check continuity
        LOG(info) << "Check continuity" << endl;
        for (size_t i = 0; i < max_size - 1; ++i)
        {
            uint64_t start_time = rows[i].start_time_value;
            uint64_t duration = rows[i].duration_value;
            uint64_t next_start = rows[i + 1].start_time_value;
            // compare current file end time with 1 sec tolerance
            if (start_time + duration + ONE_MIN < next_start)
            {
                LOG(error) << "Recorded files continuity error" << endl;
                return list;
            }
        }

        // check max duration is greater or equal to asked duration
        LOG(info) << "Check max duration is greater or equal to asked duration" << endl;
        int64_t asked_duration = t2 - t1;
        uint64_t start_time_first_file = rows[0].start_time_value;
        uint64_t start_time_last_file = rows[max_size - 1].start_time_value;
        uint64_t duration_last_file = rows[max_size - 1].duration_value;
        uint64_t end_time_last_file = start_time_last_file + duration_last_file;
        int64_t max_possible_duration = end_time_last_file - start_time_first_file;
        if (max_possible_duration < asked_duration)
        {
            LOG(error) << "Could not retrieve asked amount of data" << endl;
            return list;
        }

        // check for t1 and t2 time present in retrieved files
        LOG(info) << "Check for t1 and t2 time present in retrieved files" << endl;
        uint64_t ut1 = (uint64_t)t1, ut2 = (uint64_t)t2;
        if (ut1 + ONE_MIN < start_time_first_file || ut2 > end_time_last_file + ONE_MIN)
        {
            LOG(error) << "Could not retrieve for exact start or end time" << endl;
            return list;
        }
    }

    for (size_t i = 0; i < max_size; i++)
    {
        VideoFileInfo file(rows[i]);

        // Check if file is present locally or if it has object name (for on-demand download)
        if (isFileExist(file.m_filePath) || !file.m_objectId.empty())
        {
            list.push_back(file);
            LOG(verbose) << "File path = " << file.m_filePath << endl;
            LOG(verbose) << "File start time = " << file.m_startTime << endl;
        }
        else
        {
            LOG(error) << "File " << file.m_filePath << " not present." << endl;
        }
    }

    getActiveLocalRecording(sensorId, t1, t2, list);

    return list;
}


std::vector<VideoFileInfo> Postgresql::getNextFileList(std::string streamId, int64_t t1)
{
    std::vector<VideoFileInfo> next_file_list;
    int64_t t2 = std::numeric_limits<int64_t>::max();
    std::vector<VideoRecordDBColumns> rows = readVideoRecord(streamId, t1, t2);

    if (rows.size() == 0)
    {
        LOG(info) << "Reached at end of file, no next file available" << endl;
        return next_file_list;
    }

    for (size_t i = 0; i < rows.size(); ++i)
    {
        string file_name = rows[i].filepath_value;
        uint64_t start_time = rows[i].start_time_value;
        string object_id = rows[i].object_id_value;
        
        // Include file if it exists locally OR if it has MinIO object name (for on-demand download)
        bool includeFile = isFileExist(file_name) || !object_id.empty();
        
        if (includeFile)
        {
            LOG(info) << "File path = " << file_name << endl;
            LOG(info) << "File start time = " << start_time << endl;
            LOG(info) << "File duration = " << rows[i].duration_value << endl;
            LOG(info) << "File FPS = " << rows[i].filefps_value << endl;
            LOG(info) << "File object name = " << object_id << endl;
            
                next_file_list.push_back(rows[i]);
        }
        else
        {
            LOG(error) << "File " << file_name << " not present locally and no MinIO object name available." << endl;
        }
    }
    return next_file_list;
}

VideoFileInfo Postgresql::getInProgressRecordFile(std::string streamId, int64_t startTime)
{
    LOG(info) << "Postgresql::getInProgressRecordFile stream id: " << streamId << " startTime: " << startTime << endl;
    VideoFileInfo file;

    VideoRecordDBColumns dbRow;
    VideoRecordDBColumns row = readInProgressVideoRecord(streamId, startTime);

    if (isFileExist(row.filepath_value))
    {
        file = row;
        LOG(verbose) << "DBG File path = " << file.m_filePath << endl;
        LOG(verbose) << "DBG File start time = " << file.m_startTime << endl;
    }
    else
    {
        LOG(error) << "File " << row.filepath_value << " not present." << endl;
    }

    return file;
}

VideoFileInfo Postgresql::getRecordFileInfo(std::string streamId, int64_t startTime)
{
    VideoFileInfo file;

    VideoRecordDBColumns dbRow;
    VideoRecordDBColumns row = readVideoRecordExactMatch(streamId, startTime);

    if (isFileExist(row.filepath_value))
    {
        file = row;
        LOG(verbose) << "DBG File path = " << file.m_filePath << endl;
        LOG(verbose) << "DBG File start time = " << file.m_startTime << endl;
    }
    else
    {
        LOG(error) << "File " << row.filepath_value << " not present." << endl;
    }

    return file;
}

int Postgresql::getAllStreams(std::vector<shared_ptr<StreamInfo>> &streamInfo, const std::string &deviceId)
{
    if (deviceId.empty())
    {
        LOG(error) << "Invalid VMS ID provided" << endl;
        return -1;
    }

    vector<SensorDetailsDBColumns> rowArray = readSensorDetails(deviceId);
    for (uint32_t i = 0; i < rowArray.size(); i++)
    {
        SensorDetailsDBColumns row = rowArray[i];

        /* Main Stream */
        shared_ptr<StreamInfo> main_stream(new StreamInfo);
        getMainStreamFromDB(main_stream, row);

        if (main_stream->live_url.empty() == false)
        {
            streamInfo.push_back(main_stream);
        }

        /* Sub Streams */
        vector<SensorStreamsDBColumns> sub_stream = Postgresql::getInstance()->readAllStreamsForGivenSensorID(row.sensor_id_value);
        for (uint32_t i = 0; i < sub_stream.size(); i++)
        {
            if (sub_stream[i].sensor_id_value == row.sensor_id_value)
            {
                shared_ptr<StreamInfo> stream(new StreamInfo);
                getSubStreamFromDB(stream, sub_stream[i], row.name_value);

                if (stream->live_url.empty() == false)
                {
                    streamInfo.push_back(stream);
                }
            }
        }
    }

    return 0;
}

int Postgresql::getAllSensors(vector<shared_ptr<SensorInfo>> &sensorInfo, const std::string &deviceId)
{
    if (deviceId.empty())
    {
        LOG(error) << "Invalid VMS ID provided" << endl;
        return -1;
    }

    vector<SensorDetailsDBColumns> rowArray = readSensorDetails(deviceId);
    for (uint32_t i = 0; i < rowArray.size(); i++)
    {
        SensorDetailsDBColumns row = rowArray[i];
        shared_ptr<SensorInfo> sensor(new SensorInfo);
        getSensorInfoFromDB(sensor, row);
        sensorInfo.push_back(sensor);
    }

    return 0;
}

bool Postgresql::isSensorExists(const shared_ptr<SensorInfo> &in_device, const std::string &deviceId)
{
    return findExistingSensor(in_device, deviceId) != nullptr;
}

shared_ptr<SensorInfo> Postgresql::findExistingSensor(const shared_ptr<SensorInfo> &in_device, const std::string &deviceId)
{
    vector<shared_ptr<SensorInfo>> sensors;
    getAllSensors(sensors, deviceId);
    for (auto it = sensors.begin(); it != sensors.end(); ++it)
    {
        shared_ptr<SensorInfo> sensor = *it;
        if (in_device->streams.size() > 0 && sensor->streams.size() > 0)
        {
            shared_ptr<StreamInfo> in_stream = in_device->streams[0];
            shared_ptr<StreamInfo> stream = sensor->streams[0];
            if (stream->live_url == in_stream->live_url && stream->live_url.empty() == false && in_stream->live_url.empty() == false)
            {
                return sensor;
            }
        }
        if ((sensor->type == SENSOR_TYPE_ONVIF) && (sensor->ip == in_device->ip))
        {
            return sensor;
        }
        if (sensor->id == in_device->id && in_device->type == SENSOR_TYPE_FILE)
        {
            return sensor;
        }
    }
    return nullptr;
}

shared_ptr<SensorInfo> Postgresql::searchSensorAndGetSensorInfo(const string &searchSensorId, const std::string &deviceId)
{
    LOG(verbose) << "searchSensorId: " << searchSensorId << " deviceId: " << deviceId << endl;

    // OPTIMIZATION: Direct query instead of fetching all sensors
    // Step 1: Try to find by sensor_id directly
    SensorDetailsDBColumns sensorRow = readSensorDetails(deviceId, searchSensorId);
    if (!sensorRow.sensor_id_value.empty())
    {
        // Found sensor by ID - build SensorInfo
        shared_ptr<SensorInfo> sensor(new SensorInfo);
        getSensorInfoFromDB(sensor, sensorRow);
        LOG(verbose) << "Found sensor by sensor_id: " << searchSensorId << endl;
        return sensor;
    }

    // Step 2: Not found by sensor_id - try to find by stream_id
    SensorStreamsDBColumns streamRow = readSensorStreams(searchSensorId);
    if (!streamRow.stream_id_value.empty() && !streamRow.sensor_id_value.empty())
    {
        // Found stream - get the parent sensor
        SensorDetailsDBColumns parentSensorRow = readSensorDetails(deviceId, streamRow.sensor_id_value);
        if (!parentSensorRow.sensor_id_value.empty())
        {
            shared_ptr<SensorInfo> sensor(new SensorInfo);
            getSensorInfoFromDB(sensor, parentSensorRow);
            LOG(verbose) << "Found sensor by stream_id: " << searchSensorId << " -> sensor: " << streamRow.sensor_id_value << endl;
            return sensor;
        }
    }

    LOG(error) << "device " << searchSensorId << " not found" << endl;
    return nullptr;
}

int Postgresql::setDbVersion(DbDetailsColumns &row)
{
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement with parameterized query using safe macros */
    std::string queryTemplate = "INSERT INTO " + DbDetailsColumns::table_name + "(";
    
    APPEND_COLUMN(DbDetailsColumns::row_id, row.row_id_value, queryTemplate)
    APPEND_COLUMN(DbDetailsColumns::db_version, row.db_version_value, queryTemplate)
    APPEND_COLUMN(DbDetailsColumns::created_date_time, currentUtcTime, queryTemplate)
    APPEND_COLUMN(DbDetailsColumns::modified_date_time, currentUtcTime, queryTemplate)
    queryTemplate.pop_back(); // Remove trailing comma
    
    // Build parameterized values using safe macros
    std::vector<std::string> params;
    APPEND_COLUMN_VALUE(row.row_id_value, params)
    APPEND_COLUMN_VALUE(row.db_version_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(queryTemplate, params);
    queryTemplate += " ON CONFLICT (" +
                 DbDetailsColumns::row_id + ") DO NOTHING;";

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

DbDetailsColumns Postgresql::getDbVersion()
{
    DbDetailsColumns row;
    queryResult result;
    std::string queryTemplate = "SELECT * FROM " + DbDetailsColumns::table_name + ";";
    LOG(info) << "SQL query template: " << queryTemplate << endl;
    if (!executeQuery(queryTemplate, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, DbDetailsColumns::db_version))
            {
                row.db_version_value = column.second;
            }
            else if (iequals(column.first, DbDetailsColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, DbDetailsColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
        }
    }

    LOG(info) << "Getting DB version: " << row.db_version_value << endl;
    return row;
}

int Postgresql::updateFileProtectionInDb(bool fileProtection, string filePath)
{
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement to update the record */
    std::string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name + " SET " +
                 VideoRecordDBColumns::file_protection + " = " + PARAM_PLACEHOLDER(0) + "" +
                 " , " + VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + "" +
                 " WHERE " + VideoRecordDBColumns::file_path + " = " + PARAM_PLACEHOLDER(2) + ";";
    std::vector<std::string> params = {fileProtection ? "1" : "0", currentUtcTime, filePath};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Postgresql::updateFilesProtectionInDb(bool fileProtection, const std::vector<string>& filePaths)
{
    if (filePaths.empty())
    {
        return 0; // Nothing to update
    }

    string currentUtcTime = getCurrentUtcTime();

    // Use QueryBuilder to safely build WHERE IN clause to prevent SQL injection (PostgreSQL-specific)
    std::string whereInClause = QueryBuilder::buildWhereInClause(VideoRecordDBColumns::file_path, filePaths, DatabaseType::PostgreSQL);
    if (whereInClause.empty())
    {
        LOG(error) << "Invalid column name: " << VideoRecordDBColumns::file_path << endl;
        return -1;
    }

    /* Create SQL statement to update multiple records in a single query */
    std::string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name + " SET " +
                 VideoRecordDBColumns::file_protection + " = " + PARAM_PLACEHOLDER(0) +
                 " , " + VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + " " +
                 whereInClause + ";";
    std::vector<std::string> params = {fileProtection ? "1" : "0", currentUtcTime};

    LOG(info) << "Batch updating file protection for " << filePaths.size() << " files" << endl;
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing batch SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Postgresql::updateObjectIdInDb(const std::string& objectId, const std::string& filePath)
{
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement to update the record */
    std::string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name + " SET " +
                 VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(0) + "" +
                 " , " + VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + "" +
                 " WHERE " + VideoRecordDBColumns::file_path + " = " + PARAM_PLACEHOLDER(2) + ";";
    std::vector<std::string> params = {objectId, currentUtcTime, filePath};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Postgresql::resetProtectedFlagsInDb()
{
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement to update the record */
    std::string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name + " SET " + VideoRecordDBColumns::file_protection + " = " + PARAM_PLACEHOLDER(0) + " , " +
          VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + ";";
    std::vector<std::string> params = {"0", currentUtcTime};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

uint64_t Postgresql::getTotalCurrentRecordSize()
{
    uint64_t totalSize = 0;
    queryResult result;

    /* Create SQL statement to get sum of file sizes */
    std::string queryTemplate = "SELECT COALESCE(SUM(" + VideoRecordDBColumns::file_size + "), 0) as total_size FROM " + VideoRecordDBColumns::table_name + ";";

    LOG(verbose) << "SQL query template: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return totalSize;
    }

    if (!result.empty() && result[0].find("total_size") != result[0].end())
    {
        try
        {
            std::string value = result[0]["total_size"];
            if (!value.empty())
            {
                totalSize = std::stoull(value);
            }
        }
        catch (const std::exception &e)
        {
            LOG(error) << "Error converting sum to uint64_t: " << e.what() << endl;
        }
    }

    return totalSize;
}

std::vector<VideoRecordDBColumns> Postgresql::getProtectedFilesFromDB()
{
    // get the file size row from the Video records table
    vector<VideoRecordDBColumns> rows;
    queryResult result;
    std::string queryTemplate = "SELECT " + VideoRecordDBColumns::file_path + "," +
                 VideoRecordDBColumns::storage_location + "," +
                 VideoRecordDBColumns::record_config +
                 " FROM " +
                 VideoRecordDBColumns::table_name + " WHERE " +
                 VideoRecordDBColumns::file_protection + " = '1';";
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    LOG(verbose) << "SQL query template: " << queryTemplate << endl;

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

void Postgresql::createDatabaseTables()
{
    /* Create EVENTS Table */
    std::string queryTemplate = "CREATE TABLE IF NOT EXISTS " + EventDBColumns::table_name + "( " +
                 EventDBColumns::row_id + " SERIAL PRIMARY KEY, " +
                 EventDBColumns::video_path + " VARCHAR(1024) NOT NULL, " +
                 EventDBColumns::device_id + " VARCHAR(1024) NOT NULL, " +
                 EventDBColumns::sensor_id + " VARCHAR(1024) NOT NULL, " +
                 EventDBColumns::start_time + " VARCHAR(1024) NOT NULL, " +
                 EventDBColumns::end_time + " VARCHAR(1024) NOT NULL, " +
                 EventDBColumns::event_name + " VARCHAR(1024), " +
                 EventDBColumns::event_id + " VARCHAR(1024), " +
                 EventDBColumns::created_date_time + " VARCHAR(1024) NOT NULL, " +
                 EventDBColumns::modified_date_time + " VARCHAR(1024) NOT NULL );";
    LOG(verbose) << "SQL query template: Table: " << EventDBColumns::table_name << " " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }

    /* Create SENSOR_DETAILS Table */
    queryTemplate = "CREATE TABLE IF NOT EXISTS " + SensorDetailsDBColumns::table_name + "( " +
          SensorDetailsDBColumns::row_id + " SERIAL PRIMARY KEY, " +
          SensorDetailsDBColumns::device_id + " VARCHAR(1024) NOT NULL, " +
          SensorDetailsDBColumns::sensor_id + " VARCHAR(1024) NOT NULL, " +
          SensorDetailsDBColumns::sensor_hw_id + " VARCHAR(1024) NOT NULL, " +
          SensorDetailsDBColumns::username + " VARCHAR(1024), " +
          SensorDetailsDBColumns::password + " VARCHAR(1024), " +
          SensorDetailsDBColumns::name + " VARCHAR(1024), " +
          SensorDetailsDBColumns::ip + " VARCHAR(1024), " +
          SensorDetailsDBColumns::hardware + " VARCHAR(1024), " +
          SensorDetailsDBColumns::manufacturer + " VARCHAR(1024), " +
          SensorDetailsDBColumns::serial_number + " VARCHAR(1024), " +
          SensorDetailsDBColumns::firmware_version + " VARCHAR(1024), " +
          SensorDetailsDBColumns::hardware_id + " VARCHAR(1024), " +
          SensorDetailsDBColumns::location + " VARCHAR(1024), " +
          SensorDetailsDBColumns::tags + " VARCHAR(1024), " +
          SensorDetailsDBColumns::url + " VARCHAR(1024), " +
          SensorDetailsDBColumns::type + " VARCHAR(1024), " +
          SensorDetailsDBColumns::position + " VARCHAR(1024), " +
          SensorDetailsDBColumns::users + " VARCHAR(1024), " +
          SensorDetailsDBColumns::isRemoteSensor + " VARCHAR(1024), " +
          SensorDetailsDBColumns::remoteDeviceId + " VARCHAR(1024), " +
          SensorDetailsDBColumns::remoteDeviceName + " VARCHAR(1024), " +
          SensorDetailsDBColumns::remoteDeviceLocation + " VARCHAR(1024), " +
          SensorDetailsDBColumns::httpStatus + " INTEGER, " +
          SensorDetailsDBColumns::sensorStatus + " INTEGER, " +
          SensorDetailsDBColumns::created_date_time + " VARCHAR(1024) NOT NULL, " +
          SensorDetailsDBColumns::modified_date_time + " VARCHAR(1024) NOT NULL, " +
          "UNIQUE ( " + SensorDetailsDBColumns::sensor_id + " ));";
    LOG(verbose) << "SQL query template: Table: " << SensorDetailsDBColumns::table_name << " " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }

    /* Create VIDEO_RECORD_DETAILS Table */
    queryTemplate = "CREATE TABLE IF NOT EXISTS " + VideoRecordDBColumns::table_name + " ( " +
          VideoRecordDBColumns::row_id + " SERIAL PRIMARY KEY, " +
          VideoRecordDBColumns::sensor_id + " VARCHAR(1024) NOT NULL, " +
          VideoRecordDBColumns::stream_id + " VARCHAR(1024) NOT NULL, " +
          VideoRecordDBColumns::resolution + " VARCHAR(1024), " +
          VideoRecordDBColumns::start_time + " BIGINT, " +
          VideoRecordDBColumns::duration + " BIGINT, " +
          VideoRecordDBColumns::file_path + " VARCHAR(4096), " +
          VideoRecordDBColumns::file_size + " BIGINT, " +
          VideoRecordDBColumns::file_fps + " BIGINT, " +
          VideoRecordDBColumns::sensor_name + " VARCHAR(1024), " +
          VideoRecordDBColumns::record_config + " VARCHAR(1024), " +
          VideoRecordDBColumns::codec + " VARCHAR(1024), " +
          VideoRecordDBColumns::file_protection + " VARCHAR(1024) NOT NULL, " +
          VideoRecordDBColumns::metadata_file_path + " VARCHAR(4096), " +
          VideoRecordDBColumns::metadata_json + " VARCHAR(4096), " +
          VideoRecordDBColumns::object_id + " VARCHAR(4096), " +
          VideoRecordDBColumns::storage_location + " BIGINT NOT NULL DEFAULT 0, " +
          VideoRecordDBColumns::bucket_name + " VARCHAR(1024), " +
          VideoRecordDBColumns::created_date_time + " VARCHAR(1024) NOT NULL, " +
          VideoRecordDBColumns::modified_date_time + " VARCHAR(1024) NOT NULL );";
    LOG(verbose) << "SQL query template: Table: " << VideoRecordDBColumns::table_name << " " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }

    /* Create VIDEO_RECORD_SCHEDULE_DETAILS Table */
    queryTemplate = "CREATE TABLE IF NOT EXISTS " + VideoRecordScheduleDBColumns::table_name + " ( " +
          VideoRecordScheduleDBColumns::row_id + " SERIAL PRIMARY KEY, " +
          VideoRecordScheduleDBColumns::device_id + " VARCHAR(1024), " +
          VideoRecordScheduleDBColumns::sensor_id + " VARCHAR(1024) NOT NULL, " +
          VideoRecordScheduleDBColumns::stream_id + " VARCHAR(1024) NOT NULL, " +
          VideoRecordScheduleDBColumns::start_time + " VARCHAR(1024) NOT NULL, " +
          VideoRecordScheduleDBColumns::end_time + " VARCHAR(1024) NOT NULL, " +
          VideoRecordScheduleDBColumns::created_date_time + " VARCHAR(1024) NOT NULL, " +
          VideoRecordScheduleDBColumns::modified_date_time + " VARCHAR(1024) NOT NULL );";
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }

    /* Create SENSOR_STREAMS Table */
    queryTemplate = "CREATE TABLE IF NOT EXISTS " + SensorStreamsDBColumns::table_name + " ( " +
          SensorStreamsDBColumns::row_id + " SERIAL PRIMARY KEY, " +
          SensorStreamsDBColumns::sensor_id + " VARCHAR(1024) NOT NULL, " +
          SensorStreamsDBColumns::stream_id + " VARCHAR(1024) NOT NULL, " +
          SensorStreamsDBColumns::live_url + " VARCHAR(1024), " +
          SensorStreamsDBColumns::replay_url + " VARCHAR(1024), " +
          SensorStreamsDBColumns::proxy_url + " VARCHAR(1024), " +
          SensorStreamsDBColumns::resolution + " VARCHAR(1024), " +
          SensorStreamsDBColumns::frameRate + " VARCHAR(1024), " +
          SensorStreamsDBColumns::encoding + " VARCHAR(1024), " +
          SensorStreamsDBColumns::streamStatus + " INTEGER, " +
          SensorStreamsDBColumns::type + " INTEGER, " +
          SensorStreamsDBColumns::encodingProfile + " VARCHAR(1024), " +
          SensorStreamsDBColumns::encodingInterval + " VARCHAR(1024), " +
          SensorStreamsDBColumns::duration + " VARCHAR(1024), " +
          SensorStreamsDBColumns::isMainStream + " VARCHAR(1024), " +
          SensorStreamsDBColumns::isAlwaysRecording + " VARCHAR(1024), " +
          SensorStreamsDBColumns::storageLocation + " INTEGER DEFAULT 0, " +
          SensorStreamsDBColumns::bitrate + " VARCHAR(1024), " +
          SensorStreamsDBColumns::numFrames + " VARCHAR(1024), " +
          SensorStreamsDBColumns::audio_container + " VARCHAR(1024), " +
          SensorStreamsDBColumns::audio_encoding + " VARCHAR(1024), " +
          SensorStreamsDBColumns::audio_sample_rate + " VARCHAR(1024), " +
          SensorStreamsDBColumns::audio_bps + " VARCHAR(1024), " +
          SensorStreamsDBColumns::audio_channels + " VARCHAR(1024), " +
          SensorStreamsDBColumns::streamName + " VARCHAR(1024) NOT NULL DEFAULT '', " +
          SensorStreamsDBColumns::isBframesPresent + " INTEGER DEFAULT 0, " +
          SensorStreamsDBColumns::created_date_time + " VARCHAR(1024) NOT NULL, " +
          SensorStreamsDBColumns::modified_date_time + " VARCHAR(1024) NOT NULL, " +
          "UNIQUE( " + SensorStreamsDBColumns::stream_id + "), " +
          "FOREIGN KEY( " + SensorStreamsDBColumns::sensor_id + ") REFERENCES " +
          SensorDetailsDBColumns::table_name + "(" + SensorStreamsDBColumns::sensor_id + "));";
    LOG(verbose) << "SQL query template: Table: " << SensorStreamsDBColumns::table_name << " " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }

    /* Create USER_DETAILS Table */
    queryTemplate = "CREATE TABLE IF NOT EXISTS " + UserDetailsDBColumns::table_name + "( " +
          UserDetailsDBColumns::row_id + " SERIAL PRIMARY KEY, " +
          UserDetailsDBColumns::username + " VARCHAR(1024), " +
          UserDetailsDBColumns::password_hash + " VARCHAR(1024) NOT NULL, " +
          UserDetailsDBColumns::created_date_time + " VARCHAR(1024) NOT NULL, " +
          UserDetailsDBColumns::modified_date_time + " VARCHAR(1024) NOT NULL, " +
          "UNIQUE ( " + UserDetailsDBColumns::username + "));";
    LOG(verbose) << "SQL query template: Table: " << UserDetailsDBColumns::table_name << " " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }

    /* Create USER_SESSIONS Table */
    queryTemplate = "CREATE TABLE IF NOT EXISTS " + UserSessionsDBColumns::table_name + "( " +
          UserSessionsDBColumns::row_id + " SERIAL PRIMARY KEY, " +
          UserSessionsDBColumns::username + " VARCHAR(1024), " +
          UserSessionsDBColumns::session_cookie + " VARCHAR(1024) NOT NULL, " +
          UserSessionsDBColumns::cookie_max_age + " BIGINT NOT NULL, " +
          UserSessionsDBColumns::created_date_time + " VARCHAR(1024) NOT NULL, " +
          UserSessionsDBColumns::modified_date_time + " VARCHAR(1024) NOT NULL, " +
          "UNIQUE (" + UserSessionsDBColumns::session_cookie + "), " +
          "FOREIGN KEY (" + UserSessionsDBColumns::username + ") REFERENCES " +
          UserDetailsDBColumns::table_name +
          "(" + UserDetailsDBColumns::username + ") ON DELETE CASCADE ON UPDATE CASCADE);";
    LOG(verbose) << "SQL query template: Table: " << UserSessionsDBColumns::table_name << " " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }

    /* Create LOCAL_DEVICE_DETAILS Table */
    queryTemplate = "CREATE TABLE IF NOT EXISTS " + LocalDeviceDetailsDBColumns::table_name + " ("
            " enforce_single_row BOOLEAN NOT NULL DEFAULT TRUE UNIQUE, "
            + LocalDeviceDetailsDBColumns::row_id + " SERIAL PRIMARY KEY, "
            + LocalDeviceDetailsDBColumns::id + " VARCHAR(1024), "
            + LocalDeviceDetailsDBColumns::location + " VARCHAR(1024), "
            + LocalDeviceDetailsDBColumns::name + " VARCHAR(1024), "
            + LocalDeviceDetailsDBColumns::created_date_time + " VARCHAR(1024) NOT NULL, "
            + LocalDeviceDetailsDBColumns::modified_date_time + " VARCHAR(1024) NOT NULL"
            ");";

    LOG(verbose) << "SQL query template: Table: " << LocalDeviceDetailsDBColumns::table_name << " " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }

    /* Create DB_DETAILS Table */
    queryTemplate = "CREATE TABLE IF NOT EXISTS " + DbDetailsColumns::table_name + "( " +
          DbDetailsColumns::row_id + " SERIAL PRIMARY KEY, " +
          DbDetailsColumns::db_version + " VARCHAR(1024) NOT NULL, " +
          DbDetailsColumns::created_date_time + " VARCHAR(1024) NOT NULL, " +
          DbDetailsColumns::modified_date_time + " VARCHAR(1024) NOT NULL);";
    LOG(verbose) << "SQL query template: Table: " << DbDetailsColumns::table_name << " " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }

    /* Create RECORDING_STATUS table */
    queryTemplate = "CREATE TABLE IF NOT EXISTS " + RecordingStatusDBColumns::table_name + "( " +
          RecordingStatusDBColumns::row_id + " SERIAL PRIMARY KEY, " +
          RecordingStatusDBColumns::sensor_id + " VARCHAR(1024), " +
          RecordingStatusDBColumns::stream_id + " VARCHAR(1024)  NOT NULL, " +
          RecordingStatusDBColumns::recordingStatus + " INTEGER NOT NULL, " +
          RecordingStatusDBColumns::created_date_time + " VARCHAR(1024) NOT NULL, " +
          RecordingStatusDBColumns::modified_date_time + " VARCHAR(1024) NOT NULL, " +
          "UNIQUE ( " + RecordingStatusDBColumns::stream_id + " ));";

    LOG(verbose)
        << "SQL query template: Table: " << RecordingStatusDBColumns::table_name << " " << queryTemplate << std::endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }

    /* Create TEMP_VIDEO_FILES Table */
    queryTemplate = "CREATE TABLE IF NOT EXISTS " + TempFilesDBColumns::table_name + "( " +
          TempFilesDBColumns::row_id + " SERIAL PRIMARY KEY, " +
          TempFilesDBColumns::device_id + " VARCHAR(1024) NOT NULL, " +
          TempFilesDBColumns::file_path + " TEXT NOT NULL UNIQUE, " +
          TempFilesDBColumns::expiry_timestamp + " BIGINT NOT NULL, " +
          TempFilesDBColumns::created_timestamp + " BIGINT NOT NULL, " +
          TempFilesDBColumns::stream_id + " VARCHAR(1024), " +
          TempFilesDBColumns::file_size + " BIGINT, " +
          TempFilesDBColumns::start_time_ms + " BIGINT DEFAULT 0, " +
          TempFilesDBColumns::end_time_ms + " BIGINT DEFAULT 0, " +
          TempFilesDBColumns::file_type + " VARCHAR(16) DEFAULT '', " +
          TempFilesDBColumns::container_format + " VARCHAR(32) DEFAULT '', " +
          TempFilesDBColumns::created_date_time + " VARCHAR(1024) NOT NULL, " +
          TempFilesDBColumns::modified_date_time + " VARCHAR(1024) NOT NULL );";
    LOG(verbose) << "SQL query: Table: " << TempFilesDBColumns::table_name << " " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }

    /* Migrate existing tables: add new columns if missing */
    queryTemplate = "ALTER TABLE " + TempFilesDBColumns::table_name +
          " ADD COLUMN IF NOT EXISTS " + TempFilesDBColumns::start_time_ms + " BIGINT DEFAULT 0;";
    executeQuery(queryTemplate);

    queryTemplate = "ALTER TABLE " + TempFilesDBColumns::table_name +
          " ADD COLUMN IF NOT EXISTS " + TempFilesDBColumns::end_time_ms + " BIGINT DEFAULT 0;";
    executeQuery(queryTemplate);

    queryTemplate = "ALTER TABLE " + TempFilesDBColumns::table_name +
          " ADD COLUMN IF NOT EXISTS " + TempFilesDBColumns::file_type + " VARCHAR(16) DEFAULT '';";
    executeQuery(queryTemplate);

    queryTemplate = "ALTER TABLE " + TempFilesDBColumns::table_name +
          " ADD COLUMN IF NOT EXISTS " + TempFilesDBColumns::container_format + " VARCHAR(32) DEFAULT '';";
    executeQuery(queryTemplate);

    /* Create indexes for TEMP_VIDEO_FILES table performance */
    queryTemplate = "CREATE INDEX IF NOT EXISTS idx_temp_files_expiry ON " + 
          TempFilesDBColumns::table_name + " (" + TempFilesDBColumns::expiry_timestamp + ");";
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error creating temp files expiry index: " << queryTemplate << endl;
    }

    queryTemplate = "CREATE INDEX IF NOT EXISTS idx_temp_files_device ON " + 
          TempFilesDBColumns::table_name + " (" + TempFilesDBColumns::device_id + ");";
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error creating temp files device index: " << queryTemplate << endl;
    }

    queryTemplate = "CREATE INDEX IF NOT EXISTS idx_temp_files_stream_time ON " +
          TempFilesDBColumns::table_name + " (" +
          TempFilesDBColumns::device_id + ", " +
          TempFilesDBColumns::stream_id + ", " +
          TempFilesDBColumns::start_time_ms + ", " +
          TempFilesDBColumns::end_time_ms + ", " +
          TempFilesDBColumns::file_type + ");";
    if (!executeQuery(queryTemplate))
    {
        LOG(error) << "Error creating temp files stream_time index: " << queryTemplate << endl;
    }

    DbDetailsColumns db_detail_row;
    db_detail_row.db_version_value = VST_DB_VERSION;
    db_detail_row.row_id_value = "0";
    setDbVersion(db_detail_row);

    /**
     * username is UNIQUE in USER_DETAILS, the query will fail harmlessly
     * if admin user already exists on reboot
     */
    UserDetailsDBColumns row;
    row.username_value = DEFAULT_ADMIN_USERNAME;
    string passwordHash;
    vst_common::getSha256(DEFAULT_ADMIN_PASSWORD, passwordHash);
    row.password_hash_value = passwordHash;
    setUserDetail(row);
}

VmsErrorCode Postgresql::getMainStreamFromDB(shared_ptr<StreamInfo> &mainStream, const SensorDetailsDBColumns &sensorDetails)
{
    if (mainStream.get() == nullptr)
    {
        return VmsErrorCode::VMSInternalError;
    }
    SensorStreamsDBColumns stream_row = readSensorStreams(sensorDetails.sensor_id_value);

    mainStream->id = mainStream->sensorId = sensorDetails.sensor_id_value;
    mainStream->isMainStream = stream_row.isMainStream_value == "true" ? true : false;
    mainStream->replay_url = stream_row.replay_url_value;
    mainStream->live_proxy_url = stream_row.proxy_url_value;
    mainStream->name = sensorDetails.name_value;
    mainStream->live_url = stream_row.live_url_value;
    mainStream->stream_type = (StreamType)stream_row.streamType_value;
    mainStream->storageLocation = static_cast<StreamStorageType>(stream_row.storageLocation_value);

    SensorVideoEncoderSettingsValues enc_values;
    enc_values.encoding = stream_row.encoding_value;
    enc_values.frameRate = stream_row.frameRate_value;
    enc_values.resolution = stream_row.resolution_value;
    enc_values.encodingInterval = stream_row.encodingInterval_value;
    enc_values.numFrames = stream_row.numFrames_value;
    enc_values.bitrate = stream_row.bitrate_value;
    enc_values.isBframesPresent = (stream_row.isBframesPresent_value == 1);
    if (sensorDetails.type_value == SENSOR_TYPE_NVSTREAM)
    {
        mainStream->live_proxy_url = stream_row.replay_url_value;
        enc_values.container = sensorDetails.hardware_id_value;
    }

    SensorAudioEncoderSettingsValues audioenc_values;
    audioenc_values.encoding = stream_row.audio_encoding_value;
    audioenc_values.container = stream_row.audio_container_value;
    audioenc_values.sample_rate = stream_row.audio_sample_rate_value;
    audioenc_values.bits_per_sample = stream_row.audio_bps_value;
    audioenc_values.channels = stream_row.audio_channels_value;

    mainStream->duration = stringToInt(stream_row.duration_value, -1);
    mainStream->updateVideoEncoderValues(enc_values, false);
    mainStream->updateAudioEncoderValues(audioenc_values, false);

    // Pass false for updateDB - this is a read operation, don't write status back to DB
    mainStream->updateErrorStatus(std::make_pair(static_cast<StreamStatus>(stream_row.streamStatus_value),
        translateStreamStatusToString(static_cast<StreamStatus>(stream_row.streamStatus_value))), false);

    return VmsErrorCode::NoError;
}

VmsErrorCode Postgresql::getSubStreamFromDB(shared_ptr<StreamInfo> &subStream, const SensorStreamsDBColumns &streamDetails, const string device_name)
{
    if (subStream.get() == nullptr)
    {
        return VmsErrorCode::VMSInternalError;
    }

    subStream->id = streamDetails.stream_id_value;
    subStream->isMainStream = false;
    subStream->replay_url = streamDetails.replay_url_value;
    subStream->live_proxy_url = streamDetails.proxy_url_value;
    // Use stream_name from database if available, otherwise fall back to device_name
    subStream->name = streamDetails.streamName_value.empty() ? device_name : streamDetails.streamName_value;
    subStream->live_url = streamDetails.live_url_value;
    subStream->settings.encoderValues.encoding = streamDetails.encoding_value;
    subStream->settings.encoderValues.frameRate = streamDetails.frameRate_value;
    subStream->settings.encoderValues.resolution = streamDetails.resolution_value;
    subStream->settings.encoderValues.numFrames = streamDetails.numFrames_value;
    subStream->settings.encoderValues.bitrate = streamDetails.bitrate_value;
    subStream->settings.encoderValues.isBframesPresent = (streamDetails.isBframesPresent_value == 1);
    subStream->stream_type = (StreamType)streamDetails.streamType_value;
    subStream->storageLocation = static_cast<StreamStorageType>(streamDetails.storageLocation_value);

    // Add audio encoder values to stream
    subStream->settings.audioEncoderValues.encoding = streamDetails.audio_encoding_value;
    subStream->settings.audioEncoderValues.container = streamDetails.audio_container_value;
    subStream->settings.audioEncoderValues.sample_rate = streamDetails.audio_sample_rate_value;
    subStream->settings.audioEncoderValues.bits_per_sample = streamDetails.audio_bps_value;
    subStream->settings.audioEncoderValues.channels = streamDetails.audio_channels_value;

    // Pass false for updateDB - this is a read operation, don't write status back to DB
    subStream->updateErrorStatus(std::make_pair(static_cast<StreamStatus>(streamDetails.streamStatus_value),
        translateStreamStatusToString(static_cast<StreamStatus>(streamDetails.streamStatus_value))), false);

    return VmsErrorCode::NoError;
}

VmsErrorCode Postgresql::getSensorInfoFromDB(shared_ptr<SensorInfo> &sensorInfo, const SensorDetailsDBColumns &sensorDetails)
{
    if (sensorInfo.get() == nullptr)
    {
        return VmsErrorCode::VMSInternalError;
    }

    Json::Value origin;
    Json::Value geoLocation;
    Json::Value coordinates;
    Json::Value position;
    sensorInfo->id = sensorDetails.sensor_id_value;
    sensorInfo->sensorId = sensorDetails.sensor_hw_id_value;
    sensorInfo->hardware = sensorDetails.hardware_value;
    sensorInfo->manufacturer = sensorDetails.manufacturer_value;
    sensorInfo->serial_number = sensorDetails.serial_number_value;
    sensorInfo->firmware_version = sensorDetails.firmware_version_value;
    sensorInfo->hardware_id = sensorDetails.hardware_id_value;
    sensorInfo->location = sensorDetails.location_value;
    sensorInfo->tags = sensorDetails.tags_value;
    sensorInfo->url = sensorDetails.url_value;
    sensorInfo->user = sensorDetails.username_value;
    sensorInfo->password = sensorDetails.password_value;
    sensorInfo->name = sensorDetails.name_value;
    sensorInfo->ip = sensorDetails.ip_value;
    sensorInfo->type = sensorDetails.type_value;
    sensorInfo->isAutoDiscovered = true;
    sensorInfo->isRemoteSensor = sensorDetails.isRemoteSensor_value == "true" ? true : false;
    sensorInfo->remoteDeviceId = sensorDetails.remoteDeviceId_value;
    sensorInfo->remoteDeviceName = sensorDetails.remoteDeviceName_value;
    sensorInfo->remoteDeviceLocation = sensorDetails.remoteDeviceLocation_value;
    sensorInfo->sensorStatus = static_cast<SensorStatusEvent>(sensorDetails.sensorStatus_value);
    sensorInfo->httpStatusCode = translateVmsErrorCodeToCameraHttpErrorCode(translateCameraHttpErrorCodeToVmsErrorCode(sensorDetails.httpStatus_value));
    position = stringToJson(sensorDetails.position_value);
    if (position == Json::nullValue)
    {
        LOG(error) << "Getting invalid position object" << endl;
        return VmsErrorCode::VMSInternalError;
    }

    sensorInfo->position.direction = position.get("direction", EMPTY_STRING).asString();
    sensorInfo->position.depth = position.get("depth", EMPTY_STRING).asString();
    sensorInfo->position.fieldOfView = position.get("field_of_view", EMPTY_STRING).asString();
    origin = position.get("origin", Json::nullValue);
    geoLocation = position.get("geo_location", Json::nullValue);
    coordinates = position.get("coordinates", Json::nullValue);
    if (origin != Json::nullValue)
    {
        sensorInfo->position.origin.first = origin.get("latitude", EMPTY_STRING).asString();
        sensorInfo->position.origin.second = origin.get("longitude", EMPTY_STRING).asString();
    }
    if (geoLocation != Json::nullValue)
    {
        sensorInfo->position.geoLocation.first = geoLocation.get("latitude", EMPTY_STRING).asString();
        sensorInfo->position.geoLocation.second = geoLocation.get("longitude", EMPTY_STRING).asString();
    }
    if (coordinates != Json::nullValue)
    {
        sensorInfo->position.coordinates.first = coordinates.get("x", EMPTY_STRING).asString();
        sensorInfo->position.coordinates.second = coordinates.get("y", EMPTY_STRING).asString();
    }
    if (sensorInfo->type == SENSOR_TYPE_RTSP)
    {
        sensorInfo->isAutoDiscovered = false;
    }
    sensorInfo->addUsersFromString(sensorDetails.users_value);
    /* Main Stream - skip for file-based sensors as they store streams in SENSOR_STREAMS table */
    if (sensorInfo->type != SENSOR_TYPE_FILE)
    {
        shared_ptr<StreamInfo> main_stream(new StreamInfo);
        getMainStreamFromDB(main_stream, sensorDetails);

        if (main_stream->live_url.empty() == false)
        {
            sensorInfo->streams.push_back(main_stream);
        }
    }
    /* Sub Streams - for file-based sensors, process all streams regardless of isMainStream flag */
    vector<SensorStreamsDBColumns> sub_stream = readAllStreamsForGivenSensorID(sensorInfo->id);
    for (uint32_t i = 0; i < sub_stream.size(); i++)
    {
        // For non-file sensors, skip main streams as they're handled above
        if (sensorInfo->type != SENSOR_TYPE_FILE && sub_stream[i].isMainStream_value == "true")
        {
            continue;
        }
        if (sub_stream[i].sensor_id_value == sensorDetails.sensor_id_value)
        {
            shared_ptr<StreamInfo> stream(new StreamInfo);
            getSubStreamFromDB(stream, sub_stream[i], sensorInfo->name);
            stream->sensorId = sensorInfo->id;
            
            // For file-based sensors, always add streams (they have proxy_url instead of live_url)
            if (sensorInfo->type == SENSOR_TYPE_FILE || stream->live_url.empty() == false)
            {
                // Set isMainStream to false initially - will be corrected after all streams are loaded
                stream->isMainStream = false;
                sensorInfo->streams.push_back(stream);
            }
        }
    }
    
    // Ensure proper main stream assignment: first stream is main, others are not
    if (sensorInfo->streams.size() > 0)
    {
        for (size_t i = 0; i < sensorInfo->streams.size(); ++i)
        {
            sensorInfo->streams[i]->isMainStream = (i == 0);
        }
    }
    
    return VmsErrorCode::NoError;
}

/**
 * Select unique sensor IDs from the sensor details table, but only for those sensors that
 * have corresponding entries in the video record details table.
 */
VmsErrorCode Postgresql::getSensorIdsWithRecordingTimelines(std::unordered_set<std::string> &sensorIds)
{
    queryResult result;
    std::string queryTemplate = "SELECT DISTINCT sd." + DBColumns::sensor_id +
                      " FROM " + SensorDetailsDBColumns::table_name + " sd INNER JOIN " +
                      VideoRecordDBColumns::table_name + " vrd ON sd." +
                      DBColumns::sensor_id + " = vrd." + DBColumns::sensor_id + ";";

    LOG(verbose) << "SQL query template: " << queryTemplate << std::endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, result))
    {
        LOG(error) << "Error executing SQL statement: " << queryTemplate << std::endl;
        return VmsErrorCode::VMSInternalError;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, DBColumns::sensor_id))
            {
                sensorIds.insert(column.second);
            }
            else
            {
                LOG(warning) << "Sensor ID not found in row" << endl;
            }
        }
    }

    return VmsErrorCode::NoError;
}

int Postgresql::updateFileProtectionAndObjectIdInDb(bool fileProtection, const std::string& objectId, const std::string& filePath)
{
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement to update the record */
    std::string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name + " SET " +
                 VideoRecordDBColumns::file_protection + " = " + PARAM_PLACEHOLDER(0) + " , " +
                 VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(1) + "" +
                 " , " + VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(2) + "" +
                 " WHERE " + VideoRecordDBColumns::file_path + " = " + PARAM_PLACEHOLDER(3) + ";";
    std::vector<std::string> params = {fileProtection ? "1" : "0", objectId, currentUtcTime, filePath};
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

// Temp Files operations implementation for PostgreSQL
int Postgresql::insertTempFileRecord(TempFilesDBColumns &row)
{
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement with parameterized query using safe macros */
    std::string queryTemplate = "INSERT INTO " + TempFilesDBColumns::table_name + "(";
    
    APPEND_COLUMN(TempFilesDBColumns::device_id, row.device_id_value, queryTemplate)
    APPEND_COLUMN(TempFilesDBColumns::file_path, row.file_path_value, queryTemplate)
    APPEND_COLUMN(TempFilesDBColumns::expiry_timestamp, std::to_string(row.expiry_timestamp_value), queryTemplate)
    APPEND_COLUMN(TempFilesDBColumns::created_timestamp, std::to_string(row.created_timestamp_value), queryTemplate)
    APPEND_COLUMN(TempFilesDBColumns::stream_id, row.stream_id_value, queryTemplate)
    APPEND_COLUMN(TempFilesDBColumns::file_size, std::to_string(row.file_size_value), queryTemplate)
    APPEND_COLUMN(TempFilesDBColumns::start_time_ms, std::to_string(row.start_time_ms_value), queryTemplate)
    APPEND_COLUMN(TempFilesDBColumns::end_time_ms, std::to_string(row.end_time_ms_value), queryTemplate)
    APPEND_COLUMN(TempFilesDBColumns::file_type, row.file_type_value, queryTemplate)
    APPEND_COLUMN(TempFilesDBColumns::container_format, row.container_format_value, queryTemplate)
    APPEND_COLUMN(TempFilesDBColumns::created_date_time, currentUtcTime, queryTemplate)
    APPEND_COLUMN(TempFilesDBColumns::modified_date_time, currentUtcTime, queryTemplate)
    queryTemplate.pop_back(); // Remove trailing comma
    
    // Build parameterized values using safe macros
    std::vector<std::string> params;
    APPEND_COLUMN_VALUE(row.device_id_value, params)
    APPEND_COLUMN_VALUE(row.file_path_value, params)
    APPEND_COLUMN_VALUE_INT(row.expiry_timestamp_value, params)
    APPEND_COLUMN_VALUE_INT(row.created_timestamp_value, params)
    APPEND_COLUMN_VALUE(row.stream_id_value, params)
    APPEND_COLUMN_VALUE_INT(row.file_size_value, params)
    APPEND_COLUMN_VALUE_INT(row.start_time_ms_value, params)
    APPEND_COLUMN_VALUE_INT(row.end_time_ms_value, params)
    APPEND_COLUMN_VALUE(row.file_type_value, params)
    APPEND_COLUMN_VALUE(row.container_format_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(queryTemplate, params);
    queryTemplate += " ON CONFLICT (" + TempFilesDBColumns::file_path + ") " +
                "DO UPDATE SET " +
                TempFilesDBColumns::expiry_timestamp + " = EXCLUDED." + TempFilesDBColumns::expiry_timestamp + ", " +
                TempFilesDBColumns::modified_date_time + " = EXCLUDED." + TempFilesDBColumns::modified_date_time + ";";

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    
    LOG(info) << "Inserted temp file record: " << row.file_path_value << endl;
    return 0;
}

int Postgresql::deleteTempFileRecord(const std::string& filePath)
{
    // Use parameterized query to prevent SQL injection
    std::string queryTemplate = "DELETE FROM " + TempFilesDBColumns::table_name + 
                               " WHERE " + TempFilesDBColumns::file_path + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {filePath};

    LOG(verbose) << "SQL query template: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }

    LOG(info) << "Deleted temp file record: " << filePath << endl;
    return 0;
}

std::vector<TempFilesDBColumns> Postgresql::getAllTempFiles()
{
    std::vector<TempFilesDBColumns> result;
    queryResult queryRes;
    
    string sql = "SELECT " +
                TempFilesDBColumns::device_id + ", " +
                TempFilesDBColumns::file_path + ", " +
                TempFilesDBColumns::expiry_timestamp + ", " +
                TempFilesDBColumns::created_timestamp + ", " +
                TempFilesDBColumns::stream_id + ", " +
                TempFilesDBColumns::file_size + ", " +
                TempFilesDBColumns::start_time_ms + ", " +
                TempFilesDBColumns::end_time_ms + ", " +
                TempFilesDBColumns::file_type + ", " +
                TempFilesDBColumns::container_format +
                " FROM " + TempFilesDBColumns::table_name +
                " ORDER BY " + TempFilesDBColumns::created_timestamp + ";";

    LOG(verbose) << "SQL query: " << sql << endl;

    if (!executeQuery(sql, queryRes))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return result;
    }

    for (auto entries : queryRes)
    {
        TempFilesDBColumns record;
        for (auto column : entries)
        {
            if (iequals(column.first, TempFilesDBColumns::device_id))
            {
                record.device_id_value = column.second;
            }
            else if (iequals(column.first, TempFilesDBColumns::file_path))
            {
                record.file_path_value = column.second;
            }
            else if (iequals(column.first, TempFilesDBColumns::expiry_timestamp))
            {
                record.expiry_timestamp_value = stringToLong(column.second);
            }
            else if (iequals(column.first, TempFilesDBColumns::created_timestamp))
            {
                record.created_timestamp_value = stringToLong(column.second);
            }
            else if (iequals(column.first, TempFilesDBColumns::stream_id))
            {
                record.stream_id_value = column.second;
            }
            else if (iequals(column.first, TempFilesDBColumns::file_size))
            {
                record.file_size_value = stringToLong(column.second);
            }
            else if (iequals(column.first, TempFilesDBColumns::start_time_ms))
            {
                record.start_time_ms_value = stringToLong(column.second);
            }
            else if (iequals(column.first, TempFilesDBColumns::end_time_ms))
            {
                record.end_time_ms_value = stringToLong(column.second);
            }
            else if (iequals(column.first, TempFilesDBColumns::file_type))
            {
                record.file_type_value = column.second;
            }
            else if (iequals(column.first, TempFilesDBColumns::container_format))
            {
                record.container_format_value = column.second;
            }
        }
        
        // Skip records with invalid file paths (likely corrupted old data)
        if (record.file_path_value.length() < 10 || 
            (record.file_path_value.find('/') == string::npos && 
             record.file_path_value.find('-') != string::npos))
        {
            LOG(warning) << "Skipping temp file record with invalid path (likely corrupted): " 
                       << record.file_path_value << endl;
            continue;
        }
        
        result.push_back(record);
    }

    return result;
}

int Postgresql::cleanupTempFileRecords(int64_t olderThanTimestamp)
{
    std::vector<std::string> params = {std::to_string(olderThanTimestamp)};

    std::string countQuery = "SELECT COUNT(*) as record_count FROM " + TempFilesDBColumns::table_name +
                " WHERE " + TempFilesDBColumns::created_timestamp + " < " + PARAM_PLACEHOLDER(0) + ";";
    int recordCount = 0;
    queryResult queryRes;

    if (executeQuery(countQuery, params, queryRes))
    {
        for (auto& entries : queryRes)
        {
            for (auto& column : entries)
            {
                if (iequals(column.first, "record_count"))
                {
                    recordCount = stringToInt(column.second, 0);
                    break;
                }
            }
        }
    }

    std::string deleteQuery = "DELETE FROM " + TempFilesDBColumns::table_name +
                " WHERE " + TempFilesDBColumns::created_timestamp + " < " + PARAM_PLACEHOLDER(0) + ";";

    LOG(verbose) << "SQL query: " << deleteQuery << endl;

    if (!executeQuery(deleteQuery, params))
    {
        LOG(error) << "Error executing SQL stmt: " << deleteQuery << endl;
        return -1;
    }

    LOG(info) << "Cleaned up " << recordCount << " old temp file records" << endl;
    return recordCount;
}

TempFilesDBColumns Postgresql::findTempFileByStreamAndTime(
    const std::string& deviceId, const std::string& streamId,
    int64_t startTimeMs, int64_t endTimeMs,
    const std::string& fileType,
    const std::string& containerFormat)
{
    TempFilesDBColumns record;
    queryResult queryRes;

    auto now = std::chrono::system_clock::now();
    auto nowMs = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();

    const int64_t tol = TempFilesDBColumns::CACHE_TIME_TOLERANCE_MS;

    std::string queryTemplate = "SELECT " +
                TempFilesDBColumns::file_path + ", " +
                TempFilesDBColumns::expiry_timestamp + ", " +
                TempFilesDBColumns::stream_id +
                " FROM " + TempFilesDBColumns::table_name +
                " WHERE " + TempFilesDBColumns::device_id + " = " + PARAM_PLACEHOLDER(0) +
                " AND " + TempFilesDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(1) +
                " AND " + TempFilesDBColumns::start_time_ms + " BETWEEN " + PARAM_PLACEHOLDER(2) + " AND " + PARAM_PLACEHOLDER(3) +
                " AND " + TempFilesDBColumns::end_time_ms + " BETWEEN " + PARAM_PLACEHOLDER(4) + " AND " + PARAM_PLACEHOLDER(5) +
                " AND " + TempFilesDBColumns::file_type + " = " + PARAM_PLACEHOLDER(6) +
                " AND " + TempFilesDBColumns::expiry_timestamp + " > " + PARAM_PLACEHOLDER(7);

    std::vector<std::string> params = {
        deviceId, streamId,
        std::to_string(startTimeMs - tol), std::to_string(startTimeMs + tol),
        std::to_string(endTimeMs - tol), std::to_string(endTimeMs + tol),
        fileType, std::to_string(nowMs)
    };

    if (!containerFormat.empty())
    {
        queryTemplate += " AND " + TempFilesDBColumns::container_format + " = " + PARAM_PLACEHOLDER(8);
        params.push_back(containerFormat);
    }
    queryTemplate += " LIMIT 1;";

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, queryRes))
    {
        LOG(error) << "Error executing findTempFileByStreamAndTime query" << endl;
        return record;
    }

    for (auto& entries : queryRes)
    {
        for (auto& column : entries)
        {
            if (iequals(column.first, TempFilesDBColumns::file_path))
            {
                record.file_path_value = column.second;
            }
            else if (iequals(column.first, TempFilesDBColumns::expiry_timestamp))
            {
                record.expiry_timestamp_value = stringToLong(column.second);
            }
            else if (iequals(column.first, TempFilesDBColumns::stream_id))
            {
                record.stream_id_value = column.second;
            }
        }
        break;
    }

    return record;
}

int Postgresql::updateTempFileExpiry(const std::string& filePath, int64_t newExpiryTimestamp)
{
    string currentUtcTime = getCurrentUtcTime();

    std::string queryTemplate = "UPDATE " + TempFilesDBColumns::table_name +
                " SET " + TempFilesDBColumns::expiry_timestamp + " = " + PARAM_PLACEHOLDER(0) +
                ", " + TempFilesDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) +
                " WHERE " + TempFilesDBColumns::file_path + " = " + PARAM_PLACEHOLDER(2) + ";";

    std::vector<std::string> params = {
        std::to_string(newExpiryTimestamp), currentUtcTime, filePath
    };

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error updating temp file expiry for: " << filePath << endl;
        return -1;
    }

    LOG(info) << "Updated temp file expiry: " << filePath << " new expiry: " << newExpiryTimestamp << endl;
    return 0;
}

int Postgresql::queryCrashedRecordings(std::vector<VideoRecordDBColumns> &rows)
{
    LOG(info) << "Starting crash recovery - querying records with duration=" << FILE_INIT_DURATION << endl;

    queryResult result;
    std::string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name +
                                " WHERE " + VideoRecordDBColumns::duration + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {std::to_string(FILE_INIT_DURATION)};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    try
    {
        if (!executeQuery(queryTemplate, params, result))
        {
            LOG(error) << "Error querying crashed recordings - connection issue" << endl;
            return -1;  // Return -1 to indicate retry needed
        }
    }
    catch (const pqxx::broken_connection &e)
    {
        LOG(error) << "Database connection error during crash recovery: " << e.what() << endl;
        return -1;  // Return -1 to indicate retry needed
    }
    catch (const std::exception &e)
    {
        LOG(error) << "Unexpected error during crash recovery query: " << e.what() << endl;
        return -2;  // Return -2 for other errors (no retry)
    }

    if (result.empty())
    {
        LOG(info) << "No crashed recordings found - nothing to recover" << endl;
        return 0;
    }

    LOG(info) << "Found " << result.size() << " crashed recording(s) to fix" << endl;

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return 0;
}