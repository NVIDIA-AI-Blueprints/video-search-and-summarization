#ifndef AWS_IO_PKI_UTILS_H
#define AWS_IO_PKI_UTILS_H
/**
 * Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0.
 */
#include <aws/io/io.h>

#ifdef _WIN32
/* It's ok to include external headers because this is a PRIVATE header file
 * (it is usually a crime to include windows.h from header file) */
#    include <windows.h>
#endif /* _WIN32 */

#ifdef AWS_OS_APPLE
/* It's ok to include external headers because this is a PRIVATE header file */
#    include <CoreFoundation/CFArray.h>
#    include <Security/Security.h>
#endif /* AWS_OS_APPLE */

struct aws_secitem_options;
struct aws_string;

AWS_EXTERN_C_BEGIN

/**
 * Returns the path to the directory and file, respectively, which holds the
 * SSL certificate trust store on the system.
 */
AWS_IO_API const char *aws_determine_default_pki_dir(void);
AWS_IO_API const char *aws_determine_default_pki_ca_file(void);

#ifdef AWS_OS_APPLE
/**
 * Imports a PEM armored PKCS#7 public/private key pair
 * into identity for use with SecurityFramework.
 */
int aws_import_public_and_private_keys_to_identity(
    struct aws_allocator *alloc,
    CFAllocatorRef cf_alloc,
    const struct aws_byte_cursor *public_cert_chain,
    const struct aws_byte_cursor *private_key,
    CFArrayRef *identity,
    const struct aws_string *keychain_path);

/**
 * Imports a PKCS#12 file into identity for use with
 * SecurityFramework
 */
int aws_import_pkcs12_to_identity(
    CFAllocatorRef cf_alloc,
    const struct aws_byte_cursor *pkcs12_cursor,
    const struct aws_byte_cursor *password,
    CFArrayRef *identity);

/**
 * Loads PRM armored PKCS#7 certificates into certs
 * for use with custom CA.
 */
int aws_import_trusted_certificates(
    struct aws_allocator *alloc,
    CFAllocatorRef cf_alloc,
    const struct aws_byte_cursor *certificates_blob,
    CFArrayRef *certs);

/**
 * Imports a PEM armored PKCS#7 public/private key pair
 * into protected data keychain for use with Apple Network Framework.
 * Currently only implemented for iOS.
 */
int aws_secitem_import_cert_and_key(
    struct aws_allocator *alloc,
    CFAllocatorRef cf_alloc,
    const struct aws_byte_cursor *public_cert_chain,
    const struct aws_byte_cursor *private_key,
    sec_identity_t *secitem_identity,
    const struct aws_secitem_options *secitem_options);

/**
 * Imports a PKCS#12 file into protected data keychain for use with
 * Apple Network Framework.
 * Currently only implemented for iOS.
 */
int aws_secitem_import_pkcs12(
    CFAllocatorRef cf_alloc,
    const struct aws_byte_cursor *pkcs12_cursor,
    const struct aws_byte_cursor *password,
    sec_identity_t *out_identity);

#endif /* AWS_OS_APPLE */

#ifdef _WIN32

/**
 * Returns AWS_OP_SUCCESS if we were able to successfully load the certificate and cert_store.
 *
 * Returns AWS_OP_ERR otherwise.
 */
AWS_IO_API int aws_load_cert_from_system_cert_store(
    const char *cert_path,
    HCERTSTORE *cert_store,
    PCCERT_CONTEXT *certs);

/**
 * Imports a PEM armored PKCS#7 blob into an ephemeral certificate store for use
 * as a custom CA.
 */
AWS_IO_API int aws_import_trusted_certificates(
    struct aws_allocator *alloc,
    const struct aws_byte_cursor *certificates_blob,
    HCERTSTORE *cert_store);

/**
 * Closes a cert store that was opened by aws_is_system_cert_store, aws_import_trusted_certificates,
 * or aws_import_key_pair_to_cert_context.
 */
AWS_IO_API void aws_close_cert_store(HCERTSTORE cert_store);

/**
 * Imports a PEM armored PKCS#7 public/private key pair into certs for use as a certificate with SSPI.
 */
AWS_IO_API int aws_import_key_pair_to_cert_context(
    struct aws_allocator *alloc,
    const struct aws_byte_cursor *public_cert_chain,
    const struct aws_byte_cursor *private_key,
    bool is_client_mode,
    HCERTSTORE *cert_store,
    PCCERT_CONTEXT *certs,
    HCRYPTPROV *crypto_provider,
    HCRYPTKEY *private_key_handle);

#endif /* _WIN32 */

AWS_EXTERN_C_END

#endif /* AWS_IO_PKI_UTILS_H */
