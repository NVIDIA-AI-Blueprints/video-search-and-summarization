/*
 * SPDX-FileCopyrightText: Copyright (c) 2018-2020 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
 * @file
 * <b>DeepStream Messaging API </b>
 *
 * @b Description: This file defines the DeepStream API for exchanging
 * messages with remote entities and services.
 */

/**
 * @defgroup  ee_nvmessaging_group  Messaging API
 *
 * Defines an API for exchanging messages with remote entities and services.
 *
 * @ingroup NvDsMsgApi
 * @{
 */

#ifndef __NVDS_MSGAPI_H__
#define __NVDS_MSGAPI_H__

#ifdef __cplusplus
extern "C"
{
#endif
#include <stdint.h>

/** Defines the handle used by messaging API functions. */
typedef void *NvDsMsgApiHandle;

/**
 * Defines events associated with connections to remote entities.
 */
typedef enum {
  /** Specifies that a connection attempt was Successful*/
  NVDS_MSGAPI_EVT_SUCCESS,
  /** Specifies disconnection of a connection handle. */
  NVDS_MSGAPI_EVT_DISCONNECT,
  /** Specifies that the remote service is down. */
  NVDS_MSGAPI_EVT_SERVICE_DOWN
} NvDsMsgApiEventType;

/**
 * Defines completion codes for operations in the messaging API.
 */
typedef enum {
NVDS_MSGAPI_OK,
NVDS_MSGAPI_ERR,
NVDS_MSGAPI_UNKNOWN_TOPIC
} NvDsMsgApiErrorType;

/**
  * Type definition for a "send" callback.
  *
  * @param[in] user_ptr         A context pointer passed by async_send. The
  *                             pointer may refer to any type of information
  *                             that is useful to the callback.
  * @param[in] completion_flag  The completion code from a send operation.
  */
typedef void (*nvds_msgapi_send_cb_t)(void *user_ptr,  NvDsMsgApiErrorType completion_flag);

/**
 * @brief Type definition for callback registered during subscribe.
 *
 * This callback reports any event (success or error)
 * during message consume
 * If success, this callback reports the consumed message,
 * on a subscribed topic
 *
 * @param[in]  flag                  Message Consume Status
 * @param[in]  msg                   Received message/payload
 * @param[in]  msg_len               Length of message/payload
 * @param[in]  topic                 Topic name where the message was received
 * @param[in]  user_ptr              pointer passed during subscribe() for context
 */

typedef void (*nvds_msgapi_subscribe_request_cb_t)(NvDsMsgApiErrorType flag, void *msg, int msg_len, char *topic, void *user_ptr);

/**
 * @brief  Type definition for a "handle" callback.
 *
 * This callback reports any event (success or error)
 * during a call to nvds_msgapi_connect().
 *
 * @param[in] h_ptr     A handle for the event.
 * @param[in] ds_evt    Type of the event.
 */
typedef void (*nvds_msgapi_connect_cb_t)(NvDsMsgApiHandle h_ptr, NvDsMsgApiEventType ds_evt);

/**
 * Connects to a remote agent by calling into a protocol adapter.
 *
 * @param[in] connection_str    A connection string with format
 *                              `"url;port;topic"`.
 * @param[in] connect_cb        A callback function for events associated with
 *                              the connection.
 * @param[in] config_path       A pointer to the pathname of a configuration
 *                              file passed to the protocol adapter.
 * @return A connection handle.
 */
NvDsMsgApiHandle nvds_msgapi_connect(char *connection_str, nvds_msgapi_connect_cb_t connect_cb, char *config_path);

/**
 * Sends a message synchronously over a connection.
 *
 * @param[in] h_ptr    A connection handle.
 * @param[in] topic    A pointer to a string which specifies the topic
 *                     to which to send the message.
 * @param[in] payload  A pointer to a byte array containing the message. The
 *                     message may but need not be a NULL-terminated string.
 * @param[in] nbuf     The number of bytes of data to send, including the
 *                     terminating NULL if the message is a string.
 *
 * @return  A completion code for the send operation.
 */
NvDsMsgApiErrorType nvds_msgapi_send(NvDsMsgApiHandle h_ptr, char *topic, const uint8_t *payload, size_t nbuf);

/**
 * Sends message asynchronously over a connection.
 *
 * @param[in] h_ptr         A connection handle.
 * @param[in] topic         A pointer to a string which specifies the topic
 *                          to which to send the message.
 * @param[in] payload       A pointer to a byte array containing the message.
 *                          The message may but need not be a NULL-terminated
 *                          string.
 * @param[in] nbuf          The number of bytes of data to send, including the
 *                          terminating NULL if the message is a string.
 * @param[in] send_callback A callback to be invoked when operation completes.
 * @param[in] user_ptr      A context pointer to pass to callback.
 *
 * @return  A completion code for the send operation.
 */
NvDsMsgApiErrorType nvds_msgapi_send_async(NvDsMsgApiHandle h_ptr, char  *topic, const uint8_t *payload, size_t nbuf, nvds_msgapi_send_cb_t send_callback, void *user_ptr);

/**
  * Subscribe to a remote entity for receiving messages on a particular topic(s)
  *
  * @param[in] h_ptr                     Connection handle
  * @param[in] topics                    Array of topics to subscribe for messages
  * @param[in] num_topics                num of topics
  * @param[in] cb                        A pointer to a callback function for notifying the DS event handler
  * @param[in] user_ctx                  user ptr to be passed to callback for context
  *
  * @return Status of the subscribe operation.
 */
NvDsMsgApiErrorType nvds_msgapi_subscribe (NvDsMsgApiHandle h_ptr, char ** topics, int num_topics, nvds_msgapi_subscribe_request_cb_t  cb, void *user_ctx);

/**
 * Calls into the adapter to allow for execution of undnerlying protocol logic.
 * In this call the adapter is expected to service pending incoming and
 * outgoing messages. It can also perform periodic housekeeping tasks
 * such as sending heartbeats.
 *
 * This design gives the client control over when protocol logic gets executed.
 * The client must call it periodically, according to the individual adapter's
 * requirements.
 *
 * @param[in] h_ptr     A connection handle.
 */
void nvds_msgapi_do_work(NvDsMsgApiHandle h_ptr);

/**
 * Terminates a connection.
 *
 * @param[in] h_ptr    The connection handle.
 *
 * @return  A completion code for the "terminate" operation.
 */
NvDsMsgApiErrorType nvds_msgapi_disconnect(NvDsMsgApiHandle h_ptr);

/**
 * Gets the version number of the messaging API interface supported by the
 * protocol adapter.
 *
 * @return  A pointer to a string that contains version number in
 * `"major.minor"` format.
 */
char *nvds_msgapi_getversion(void);

/**
 * Gets the name of the protocol used in the adapter.
 *
 * @return  A pointer to a string
 */
char *nvds_msgapi_get_protocol_name(void);

/**
 * Fetch the connection signature by parsing broker_connection string and cfg file
 *
 * A connection signature is a unique string used to identify a connection.
 * It is generated by parsing all the connection params provided in broker_str and cfg file
 *
 * Connection signature can be retreived only if the cfg option share-connection = 1
 *
 * @param[in] broker_str  Broker connection string used to create connection
 * @param[in] cfg         Path to config file
 * @param[out] output_str connection signature
 * @param[in] max_len     max len of output_str
 *
 * @return                Valid connection signature if success
 *                        Empty string("") in case of errors or if share-connection cfg option is not set to 1
 */

NvDsMsgApiErrorType nvds_msgapi_connection_signature(char *broker_str, char *cfg, char *output_str, int max_len);

#ifdef __cplusplus
}
#endif

#endif

/** @} */

