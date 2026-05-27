#ifndef AWS_S3_BUFFER_POOL_H
#define AWS_S3_BUFFER_POOL_H

#include <aws/common/ref_count.h>
#include <aws/io/future.h>
#include <aws/s3/s3.h>

/**
 * Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0.
 */

/**
 * Generic memory pool interface.
 * Allows consumers of aws-c-s3 to override how buffer allocation for part buffers is done.
 * Refer to docs/memory_aware_request_execution.md for details on how default implementation works.
 * WARNING: this is currently experimental feature and does not provide API stability guarantees so should be used with
 * caution. At highlevel the flow is as follows:
 * - crt scheduler queues up requests to be prepared
 * - requests being prepared will try to reserve mem (i.e. obtain a ticket) and wait until they get it before proceeding
 * - once mem is reserved requests will proceed with the pipeline
 * - request will acquire buffer from the ticket when needed
 * - ticket is released when request is done with the buffer
 * Note: in some cases pipeline can stall if new buffer cannot be allocated (ex. async writes flow).
 * In this case reserve request will indicate that not granting the ticket can block and buffer pool should try to
 * allocate ticket right away (or wait and call waker when mem is allocated for the case of async writes).
 */

AWS_PUSH_SANE_WARNING_LEVEL
AWS_EXTERN_C_BEGIN
struct aws_s3_buffer_ticket;

/**
 * aws_future<aws_s3_buffer_ticket*>
 * Buffer ticket future used for reservations.
 */
AWS_FUTURE_T_POINTER_WITH_RELEASE_DECLARATION(aws_future_s3_buffer_ticket, struct aws_s3_buffer_ticket, AWS_S3_API)

/**
 * Meta information about ticket reservation request.
 */
struct aws_s3_buffer_pool_reserve_meta {
    /* client reserving the ticket. accounts for buffer pool being shared between clients. */
    struct aws_s3_client *client;

    /* meta request ticket is being reserved for. */
    struct aws_s3_meta_request *meta_request;

    /* size of the buffer to reserve. */
    size_t size;

    /* whether not granting reservation can result in request pipeline being blocked.
     * Note: blocking is currently a terminal condition and that cannot be recovered from,
     * i.e. meta request will be stuck and not make any process.
     * As such buffer pool should either grant or error out reservation in sync.
     * This scenario currently only occurs in the async_write flows. */
    bool can_block;
};

struct aws_s3_buffer_ticket;

struct aws_s3_buffer_ticket_vtable {
    /**
     * Get buffer associated with the ticket.
     * Note: can be called multiple times and the same buffer should be returned. In some cases ticket might not be
     * claimed at all.
     */
    struct aws_byte_buf (*claim)(struct aws_s3_buffer_ticket *ticket);

    /* Implement below for custom ref count behavior. Alternatively set those to null and init the ref count. */
    struct aws_s3_buffer_ticket *(*acquire)(struct aws_s3_buffer_ticket *ticket);
    struct aws_s3_buffer_ticket *(*release)(struct aws_s3_buffer_ticket *ticket);
};

/**
 * Polymorphic ticket.
 */
struct aws_s3_buffer_ticket {
    struct aws_s3_buffer_ticket_vtable *vtable;
    struct aws_ref_count ref_count;
    void *impl;
};

AWS_S3_API struct aws_byte_buf aws_s3_buffer_ticket_claim(struct aws_s3_buffer_ticket *ticket);

AWS_S3_API struct aws_s3_buffer_ticket *aws_s3_buffer_ticket_acquire(struct aws_s3_buffer_ticket *ticket);
AWS_S3_API struct aws_s3_buffer_ticket *aws_s3_buffer_ticket_release(struct aws_s3_buffer_ticket *ticket);

struct aws_s3_buffer_pool;

struct aws_s3_buffer_pool_vtable {
    /* Reserve a ticket. Returns a future that is granted whenever reservation can be made. */
    struct aws_future_s3_buffer_ticket *(
        *reserve)(struct aws_s3_buffer_pool *pool, struct aws_s3_buffer_pool_reserve_meta meta);

    /**
     * Trim the pool. This is mostly a suggestion, which pool can decide to ignore. Triggered by CRT when
     * client has been idle for some time.
     **/
    void (*trim)(struct aws_s3_buffer_pool *pool);

    /* Implement below for custom ref count behavior. Alternatively set those to null and init the ref count. */
    struct aws_s3_buffer_pool *(*acquire)(struct aws_s3_buffer_pool *pool);
    struct aws_s3_buffer_pool *(*release)(struct aws_s3_buffer_pool *pool);
};

/**
 * Polymorphic buffer pool.
 */
struct aws_s3_buffer_pool {
    struct aws_s3_buffer_pool_vtable *vtable;
    struct aws_ref_count ref_count;
    void *impl;
};

AWS_S3_API struct aws_future_s3_buffer_ticket *aws_s3_buffer_pool_reserve(
    struct aws_s3_buffer_pool *buffer_pool,
    struct aws_s3_buffer_pool_reserve_meta meta);
AWS_S3_API void aws_s3_buffer_pool_trim(struct aws_s3_buffer_pool *buffer_pool);

AWS_S3_API struct aws_s3_buffer_pool *aws_s3_buffer_pool_acquire(struct aws_s3_buffer_pool *buffer_pool);
AWS_S3_API struct aws_s3_buffer_pool *aws_s3_buffer_pool_release(struct aws_s3_buffer_pool *buffer_pool);

/**
 * Buffer pool configuration options.
 */
struct aws_s3_buffer_pool_config {
    struct aws_s3_client *client; /* Client creating the pool. */
    size_t part_size;             /* Default part size of the client. */
    size_t max_part_size;         /* Max part size configured on the client. */
    size_t memory_limit;          /* Memory limit set on the client. */
};

/**
 * Factory to construct the pool for the given config. Passes along buffer related info configured on the client, which
 * factory may ignore when considering how to construct pool.
 * This implementation should fail if pool cannot be constructed for some reason (ex. if config params cannot be met),
 * by logging failure reason, returning null and raising aws_error.
 */
typedef struct aws_s3_buffer_pool *(aws_s3_buffer_pool_factory_fn)(struct aws_allocator *allocator,
                                                                   struct aws_s3_buffer_pool_config config);

AWS_EXTERN_C_END
AWS_POP_SANE_WARNING_LEVEL

#endif /* AWS_S3_BUFFER_POOL_H */
