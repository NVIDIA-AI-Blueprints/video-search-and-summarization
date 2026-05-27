#ifndef AWS_CAL_ED25519_H
#define AWS_CAL_ED25519_H
/**
 * Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0.
 */
#include <aws/cal/cal.h>
#include <aws/common/byte_buf.h>

AWS_PUSH_SANE_WARNING_LEVEL

struct aws_ed25519_key_pair;

AWS_EXTERN_C_BEGIN

/**
 * Generate new Ed25519 key.
 * Returns a new instance of aws_ed25519_key_pair if the key was successfully generated.
 * Otherwise returns NULL.
 * Note: keygen is not supported on all platforms and will return NULL for the key
 *  and raise AWS_ERROR_CAL_UNSUPPORTED_ALGORITHM.
 * Examples of unsupported cases:
 * - openssl pre 1.1.1 (Note: aws-lc and boringssl both expose the needed functions)
 * - win/mac builds without special flag that forces linking to libcrypto to support this
 */
AWS_CAL_API struct aws_ed25519_key_pair *aws_ed25519_key_pair_new_generate(struct aws_allocator *allocator);

/**
 * Adds one to an Ed25519 key pair's ref count.
 * Returns key_pair pointer.
 */
AWS_CAL_API struct aws_ed25519_key_pair *aws_ed25519_key_pair_acquire(struct aws_ed25519_key_pair *key_pair);

/**
 * Subtracts one from an Ed25519 key pair's ref count. If ref count reaches zero, the key pair is destroyed.
 * Always returns NULL.
 */
AWS_CAL_API struct aws_ed25519_key_pair *aws_ed25519_key_pair_release(struct aws_ed25519_key_pair *key_pair);

enum aws_ed25519_key_export_format {
    /* Export the key as raw bytes */
    AWS_CAL_ED25519_KEY_EXPORT_RAW,

    /**
     * Export the key to openssh format.
     * This will only export the key block, framing (i.e. pem) is left as exercise for the caller.
     * b64 encoding is done as convenience since common framing formats require it.
     */
    AWS_CAL_ED25519_KEY_EXPORT_OPENSSH_B64,
};

/*
 * Get public key for the key pair.
 * Key in specified format is appended to the buffer.
 * The buffer must be initialized before this call, with sufficient capacity to hold the result.
 * Use aws_ed25519_key_pair_get_public_key_size to figure out how much capacity buffer needs for a given format.
 */
AWS_CAL_API int aws_ed25519_key_pair_get_public_key(
    const struct aws_ed25519_key_pair *key_pair,
    enum aws_ed25519_key_export_format format,
    struct aws_byte_buf *out);

/**
 * Gets the size of the exported public key.
 */
AWS_CAL_API size_t aws_ed25519_key_pair_get_public_key_size(enum aws_ed25519_key_export_format format);

/*
 * Get private key for the key pair.
 * Key in specified format is appended to the buffer.
 * The buffer must be initialized before this call, with sufficient capacity to hold the result.
 * Use aws_ed25519_key_pair_get_private_key_size to figure out how much capacity buffer needs for a given format.
 */
AWS_CAL_API int aws_ed25519_key_pair_get_private_key(
    const struct aws_ed25519_key_pair *key_pair,
    enum aws_ed25519_key_export_format format,
    struct aws_byte_buf *out);

/**
 * Gets the size of the exported private key.
 */
AWS_CAL_API size_t aws_ed25519_key_pair_get_private_key_size(enum aws_ed25519_key_export_format format);

AWS_EXTERN_C_END

AWS_POP_SANE_WARNING_LEVEL

#endif /* AWS_CAL_ED25519_H */
