/* WirePlumber
 *
 * Copyright © 2024 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_BASE_DIRS_H__
#define __WIREPLUMBER_BASE_DIRS_H__

#include "defs.h"
#include "iterator.h"

G_BEGIN_DECLS

/*!
 * \brief Flags to specify lookup directories
 * \ingroup wpbasedirs
 *
 * These flags can be used to specify which directories to look for a file in.
 * The flags can be combined to search in multiple directories at once. Some
 * flags may also used to specify the type of the file being looked up or other
 * lookup parameters.
 *
 * Lookup is performed in the same order as the flags are listed here. Note that
 * if a WirePlumber-specific environment variable is set ($WIREPLUMBER_*_DIR)
 * and the equivalent WP_BASE_DIRS_ENV_* flag is specified, the lookup in other
 * directories is skipped, even if the file is not found in the
 * environment-specified directory.
 */
typedef enum { /*< flags >*/
  WP_BASE_DIRS_ENV_CONFIG = (1 << 0),       /*!< $WIREPLUMBER_CONFIG_DIR */
  WP_BASE_DIRS_ENV_DATA = (1 << 1),         /*!< $WIREPLUMBER_DATA_DIR */
  WP_BASE_DIRS_ENV_MODULE = (1 << 2),       /*!< $WIREPLUMBER_MODULE_DIR */

  WP_BASE_DIRS_XDG_CONFIG_HOME = (1 << 8),  /*!< XDG_CONFIG_HOME */
  WP_BASE_DIRS_XDG_DATA_HOME = (1 << 9),    /*!< XDG_DATA_HOME */

  WP_BASE_DIRS_XDG_CONFIG_DIRS = (1 << 10), /*!< XDG_CONFIG_DIRS */
  WP_BASE_DIRS_BUILD_SYSCONFDIR = (1 << 11), /*!< compile-time $sysconfdir (/etc) */

  WP_BASE_DIRS_XDG_DATA_DIRS = (1 << 12),   /*!< XDG_DATA_DIRS */
  WP_BASE_DIRS_BUILD_DATADIR = (1 << 13),   /*!< compile-time $datadir ($prefix/share) */

  WP_BASE_DIRS_BUILD_LIBDIR = (1 << 14),    /*!< compile-time $libdir ($prefix/lib) */

  /*! the file is a loadable module; prepend "lib" and append ".so" if needed */
  WP_BASE_DIRS_FLAG_MODULE = (1 << 24),

  /*! append "/wireplumber" to the location, except in the case of locations
      that are specified via WirePlumber-specific environment variables;
      in LIBDIR, append "/wireplumber-$API_version" instead */
  WP_BASE_DIRS_FLAG_SUBDIR_WIREPLUMBER = (1 << 25),

  WP_BASE_DIRS_CONFIGURATION =
      (WP_BASE_DIRS_ENV_CONFIG |
       WP_BASE_DIRS_XDG_CONFIG_HOME |
       WP_BASE_DIRS_XDG_CONFIG_DIRS |
       WP_BASE_DIRS_BUILD_SYSCONFDIR |
       WP_BASE_DIRS_XDG_DATA_DIRS |
       WP_BASE_DIRS_BUILD_DATADIR |
       WP_BASE_DIRS_FLAG_SUBDIR_WIREPLUMBER),

  WP_BASE_DIRS_DATA =
      (WP_BASE_DIRS_ENV_DATA |
       WP_BASE_DIRS_XDG_DATA_HOME |
       WP_BASE_DIRS_XDG_DATA_DIRS |
       WP_BASE_DIRS_BUILD_DATADIR |
       WP_BASE_DIRS_FLAG_SUBDIR_WIREPLUMBER),

  WP_BASE_DIRS_MODULE =
      (WP_BASE_DIRS_ENV_MODULE |
       WP_BASE_DIRS_BUILD_LIBDIR |
       WP_BASE_DIRS_FLAG_MODULE |
       WP_BASE_DIRS_FLAG_SUBDIR_WIREPLUMBER),
} WpBaseDirsFlags;

WP_API
gchar * wp_base_dirs_find_file (WpBaseDirsFlags flags,
    const gchar * subdir, const gchar * filename);

WP_API
WpIterator * wp_base_dirs_new_files_iterator (WpBaseDirsFlags flags,
    const gchar * subdir, const gchar * suffix);

G_END_DECLS

#endif
