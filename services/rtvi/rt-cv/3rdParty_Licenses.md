# Third-Party Licenses

This file contains the full license text for all third-party dependencies added by the vss-rt-cv container image on top of its base container.

Third-party software from the base image (NVIDIA DeepStream and the Ubuntu / Triton base) is listed in the base LICENSE file included in that image at `/opt/nvidia/deepstream/deepstream/LICENSE.txt`.

Added components: 12 system (Debian/APT) + 50 Python (PyPI) = 62 packages.

Versions are those resolved in the linux/amd64 build. Unpinned dependencies may resolve to a newer patch release on a later build; the license type is the property this file records.

---

## gir1.2-girepository-2.0:1.80.1-1

**License Type:** GPL-2+ / LGPL-2+ / LGPL-2 or MPL-1.1 / LGPL-2.1+ / BSD-2-clause / FSFAP and FSFULLR / Expat and GPL-2+ / LGPL-2+ and LGPL-2.1+ and FSFULLR and CC0-1.0 / AFL-2.0 or LGPL-2.1+ / Unicode-DFS-2016 / Expat / LGPL-3+ / Apache-2.0 with LLVM exception / LGPL-2.1+ and Kuchling-PD and Plumb-PD / bzip2-1.0.6 / CC-BY-SA-3.0 / GPL with Autoconf exception / AFL-2.0 / CC0-1.0 / FSFAP / FSFULLR / Kuchling-PD / LGPL-2 / MPL-1.1 / Plumb-PD

**Source:** Debian/APT

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Source: https://download.gnome.org/sources/gobject-introspection/
Upstream-Name: GObject Introspection

Files: *
Copyright:
 2012 Canonical Ltd
 2012-2015 Dieter Verfaillie
 2005-2008 Divmod, Inc.
 2008-2011 Johan Dahlin
 2006 Johann C. Rocholl
 2007-2008 Jürg Billeter
 2005 Matthias Clasen
 2008 Philip Van Hoof
 2008-2010 Red Hat, Inc.
 1997 Sandro Sigala
 2011 Shaun McCance
 2018 Tomasz Miąsko
 2010 Zach Goldberg
License: GPL-2+

Files:
 girepository/*.c
 girepository/*.h
 giscanner/giscannermodule.c
 giscanner/sourcescanner.c
 giscanner/sourcescanner.h
 giscanner/__init__.py
 giscanner/cachestore.py
 giscanner/gdumpparser.py
 giscanner/girparser.py
 giscanner/sourcescanner.py
 giscanner/transformer.py
 giscanner/utils.py
 giscanner/xmlwriter.py
 tools/compiler.c
 tools/generate.c
Copyright:
 2018 Christoph Reiter
 2014 Chun-wei Fan
 2008 Colin Walters
 2013 Dieter Verfaillie
 2011-2016 Dominique Leuenberger
 2016 Igor Gnatenko
 2007-2010 Johan Dahlin
 2007 Jürg Billeter
 2003-2005 Matthias Clasen
 2008 Philip Van Hoof
 2008-2013 Red Hat, Inc.
License: LGPL-2+

Files:
 girepository/cmph/*
Copyright:
 Davi de Castro Reis
 Fabiano Cupertino Botelho
License: LGPL-2 or MPL-1.1

Files:
 tests/scanner/identfilter.py
 tests/scanner/symbolfilter.py
Copyright:
 2014 Simon Feltman <sfeltman@gnome.org>
 2015 Garrett Regier <garrett.regier@riftio.com>
License: LGPL-2.1+

Files:
 giscanner/scannerlexer.l
 giscanner/scannerparser.y
Copyright:
 1997 Sandro Sigala
 2007-2008 Jürg Billeter
 2010 Andreas Rottmann
License: BSD-2-clause

Files:
 m4/introspection.m4
Copyright:
 2003-2005 Thomas Vander Stichele
 2009 Johan Dahlin
License: FSFAP and FSFULLR

Files: debian/*
Copyright:
 2023 Collabora Ltd.
 2008-2022 Debian contributors as listed in debian/changelog
 2021-2023 Simon McVittie
License: Expat and GPL-2+
Comment:
 No license was specified for the contents of debian/ prior to 2022. It
 is assumed to have been intended to be under the most restrictive of
 the upstream licenses, namely GPL-2+.
 The following contributors give permission to relicense their contributions
 to this package under either Expat, GPL-2+ or LGPL-2.1+ if desired:
 - Collabora Ltd.
 - Simon McVittie
 (Please add your name to this list if you wish to give this permission.)

# ----------------------------------------------------------------------

Files: glib/*
Comment:
 This directory is added by the Debian packaging to provide corresponding
 source code for gir/*.c, which are concatenations of doc-comments from the
 source files in glib/, in order to make it obvious that the preferred form
 for modification is included. The upstream plan is that responsibility for
 generating gir1.2-glib-2.0{,-dev} will move to the equivalent of Debian's
 src:glib2.0 during the GNOME 46 release cycle, at which point glib/ and
 gir/*.c can be dropped from src:gobject-introspection.
Copyright:
 2004-2005 Adam Weinberger
 2005-2006 Alexander Larsson
 2022 Alexander Shopov
 2021 Alexandros Theodotou
 2004 Anders Carlsson
 2001-2003 Andrew Lanoix
 2018 Arthur Demchenkov
 2001-2004 Behdad Esfahbod
 2006 Behdad Esfahbod
 2009 Benjamin Otte
 2009-2022 Canonical Ltd (Canonical Limited)
 2009-2022 Christian Hergert
 2010 Christian Kellner
 2010 Christian Persch
 2014-2015 Chun-wei Fan
 2008 Claus Tondering
 2008 Clemens N. Buss
 2008-2014 Codethink Ltd (Codethink Limited)
 2012-2013 Colin Walters
 2017-2018 Collabora Inc
 2008-2022 Collabora Ltd
 1999-2000 Craig Setera
 2000 Eazel, Inc
 2005-2023 Emmanuele Bassi
 2022 Emmanuel Fleury
 2016-2020 Endless Mobile, Inc
 2017-2022 Endless OS Foundation, LLC
 2007 Francois Gouget
 2020-2021 Frederic Martinsons
 1991-2022 Free Software Foundation, Inc
 2015 Garrett Regier
 2001-2022 GLib contributors
 2016 GNOME i18n Project for Vietnamese
 2019 GNOME
 2011 Google, Inc
 2001-2008 Hans Breuer
 2001 Hidetoshi Tajima
 2021 Iain Lane
 2018-2021 Igalia S.L
 2005-2008 Imendio AB
 2018 Iñigo Martínez
 2010 Intel Corp
 2001 James Henstridge
 2018-2019 James Westman
 2014-2018 Jan-Michael Brummer
 2005-2007 John McCutchan
 1995-1998 Josh MacDonald
 2007 Jürg Billeter
 2010 Karo Mkrtchyan
 2013-2015 Lars Uebernickel
 2006 Lukas Novotny
 2002 Manish Singh
 2005-2007 Marco Barisione
 2022 Marco Trevisan
 2001-2013 Matthias Clasen
 2010 Mikhail Zabaluev
 2004-2005 Miloslav Trmac
 2014 NICE s.r.l
 2003 Noah Levitt
 2008-2011 Nokia Corporation
 2008-2010 Novell, Inc
 2021 Ole André Vadla Ravnås
 2007 Openismus GmbH
 1998-2001 Owen Taylor
 2014-2019 Patrick Griffis
 2007 Patrick Hulin
 2012 Pavel Vasin
 2018 pdknsk
 1995-2011 Peter Mattis
 1995-2011 Peter Mattis, Spencer Kimball, Josh MacDonald, Sebastian Wilhelmi and others
 2022 Philip Withnall
 1998-2022 Red Hat, Inc
 1999-2003 Red Hat Software
 2001 Ron Steinke
 2020 Ruslan N. Marchenko
 2022 Ryan Hope
 2007-2015 Ryan Lortie
 2009-2010 Sam Thursfield
 2008 Samuel Cormier-Iijima
 1999-2000 Scott Wimer
 2007-2020 Sebastian Dröge
 2001-2003 Sebastian Wilhelmi
 1998-2001 Sebastian Wilhelmi; University of Karlsruhe
 2002-2006 Sharif FarsiWeb, Inc
 2012 Simon McVittie
 2011 Sjoerd Simons
 2002-2007 Soeren Sandmann
 1995-1998 Spencer Kimball
 2006 Stefan Westerfeld
 2011-2013 Stef Walter
 2007-2010 Sven Herzberg
 2012 Swecha telugu localisation Team
 2011-2022 systemd contributors
 2002-2022 the author(s) of GLib
 2006 The GNOME Foundation
 2007-2011 The GNOME Project
 2010 Thiago Santos
 1997-2007 Tim Janik
 2018 Tomasz Miąsko
 1999-2000 Tom Tromey
 1998-2004 Tor Lillqvist
 1995-1997 Ulrich Drepper
 2021 Unicode®, Inc
 2011 William Hua
 2018 Will Thompson
 2020 Xavier Claessens
 2021 Xavier Claessens
 2000-2004 Ximian Inc
 2014-2020 Руслан Ижбулатов
 Croatiann team
 Matthew Waters
License: LGPL-2+ and LGPL-2.1+ and FSFULLR and CC0-1.0

Files:
 glib/gio/xdgmime/xdgmime*
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright:
 2003-2004 Jonathan Blandford
 2004-2005 Matthias Clasen
 2003-2008 Red Hat, Inc
License: AFL-2.0 or LGPL-2.1+

Files:
 glib/glib/tests/NormalizationTest.txt
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright:
 2021 Unicode®, Inc.
License: Unicode-DFS-2016

Files:
 glib/gio/kqueue/*.?
 glib/*/tests/taptestrunner.py
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright:
 2011-2012 Dmitry Matveev
 2015 Remko Tronçon
License: Expat

Files:
 glib/glib/gen-unicode-tables.pl
 glib/glib/tests/gen-casefold-txt.py
 glib/glib/tests/gen-casemap-txt.py
 glib/po/po2tbl.sed.in
 glib/tools/glib-gettextize.in
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright:
 1989-2001 Free Software Foundation, Inc
 2001 Red Hat Software
 1998-1999 Tom Tromey
License: GPL-2+

Files:
 glib/gio/tests/memory-monitor-dbus.py.in
 glib/gio/tests/memory-monitor-portal.py.in
 glib/gio/tests/power-profile-monitor-dbus.py.in
 glib/gio/tests/power-profile-monitor-portal.py.in
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright: 2019-2021 Red Hat, Inc
License: LGPL-3+

Files:
 glib/.gitlab-ci/clang-format-diff.py
 glib/fuzzing/driver.c
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright: 2018, LLVM contributors
License: Apache-2.0 with LLVM exception

Files:
 glib/glib/gchecksum.c
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright:
 1995 A.M. Kuchling
 2006 Dave Benson
 2007 Emmanuele Bassi
License: LGPL-2.1+ and Kuchling-PD and Plumb-PD

Files:
 glib/glib/valgrind.h
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright:
 2000-2017, Julian Seward
License: bzip2-1.0.6

Files:
 glib/docs/reference/gio/menu-model.png
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright: unspecified
License: CC-BY-SA-3.0

Files: glib/m4macros/glib-gettext.m4
Copyright:
 1995-2002 Free Software Foundation, Inc.
 2001-2004 Red Hat, Inc.
License: GPL with Autoconf exception

# ----------------------------------------------------------------------

License: AFL-2.0
 The Academic Free License
 v. 2.0
 .
 This Academic Free License (the "License") applies to any original work
 of authorship (the "Original Work") whose owner (the "Licensor") has
 placed the following notice immediately following the copyright notice
 for the Original Work:
 .
      Licensed under the Academic Free License version 2.0
 .
 1) Grant of Copyright License. Licensor hereby grants You a world-wide,
 royalty-free, non-exclusive, perpetual, sublicenseable license to do
 the following:
 .
      a) to reproduce the Original Work in copies;
      b) to prepare derivative works ("Derivative Works") based upon the Original Work;
      c) to distribute copies of the Original Work and Derivative Works to the public;
      d) to perform the Original Work publicly; and
      e) to display the Original Work publicly.
 .
 2) Grant of Patent License. Licensor hereby grants You a world-wide,
 royalty-free, non-exclusive, perpetual, sublicenseable license, under
 patent claims owned or controlled by the Licensor that are embodied in
 the Original Work as furnished by the Licensor, to make, use, sell and
 offer for sale the Original Work and Derivative Works.
 .
 3) Grant of Source Code License. The term "Source Code" means the
 preferred form of the Original Work for making modifications to it and
 all available documentation describing how to modify the Original Work.
 Licensor hereby agrees to provide a machine-readable copy of the Source
 Code of the Original Work along with each copy of the Original Work
 that Licensor distributes.  Licensor reserves the right to satisfy this
 obligation by placing a machine-readable copy of the Source Code in an
 information repository reasonably calculated to permit inexpensive and
 convenient access by You for as long as Licensor continues to distribute
 the Original Work, and by publishing the address of that information
 repository in a notice immediately following the copyright notice that
 applies to the Original Work.
 .
 4) Exclusions From License Grant. Neither the names of Licensor, nor
 the names of any contributors to the Original Work, nor any of their
 trademarks or service marks, may be used to endorse or promote products
 derived from this Original Work without express prior written permission
 of the Licensor.  Nothing in this License shall be deemed to grant any
 rights to trademarks, copyrights, patents, trade secrets or any other
 intellectual property of Licensor except as expressly stated herein.
 No patent license is granted to make, use, sell or offer to sell
 embodiments of any patent claims other than the licensed claims defined
 in Section 2.  No right is granted to the trademarks of Licensor even if
 such marks are included in the Original Work.  Nothing in this License
 shall be interpreted to prohibit Licensor from licensing under different
 terms from this License any Original Work that Licensor otherwise would
 have a right to license.
 .
 5) This section intentionally omitted.
 .
 6) Attribution Rights. You must retain, in the Source Code of any
 Derivative Works that You create, all copyright, patent or trademark
 notices from the Source Code of the Original Work, as well as any
 notices of licensing and any descriptive text identified therein as an
 "Attribution Notice."  You must cause the Source Code for any Derivative
 Works that You create to carry a prominent Attribution Notice reasonably
 calculated to inform recipients that You have modified the Original Work.
 .
 7) Warranty of Provenance and Disclaimer of Warranty. Licensor warrants
 that the copyright in and to the Original Work and the patent rights
 granted herein by Licensor are owned by the Licensor or are sublicensed
 to You under the terms of this License with the permission of the
 contributor(s) of those copyrights and patent rights.  Except as
 expressly stated in the immediately proceeding sentence, the Original
 Work is provided under this License on an "AS IS" BASIS and WITHOUT
 WARRANTY, either express or implied, including, without limitation,
 the warranties of NON-INFRINGEMENT, MERCHANTABILITY or FITNESS FOR A
 PARTICULAR PURPOSE.  THE ENTIRE RISK AS TO THE QUALITY OF THE ORIGINAL
 WORK IS WITH YOU.  This DISCLAIMER OF WARRANTY constitutes an essential
 part of this License.  No license to Original Work is granted hereunder
 except under this disclaimer.
 .
 8) Limitation of Liability. Under no circumstances and under no legal
 theory, whether in tort (including negligence), contract, or otherwise,
 shall the Licensor be liable to any person for any direct, indirect,
 special, incidental, or consequential damages of any character arising
 as a result of this License or the use of the Original Work including,
 without limitation, damages for loss of goodwill, work stoppage, computer
 failure or malfunction, or any and all other commercial damages or losses.
 This limitation of liability shall not apply to liability for death
 or personal injury resulting from Licensor's negligence to the extent
 applicable law prohibits such limitation.  Some jurisdictions do not
 allow the exclusion or limitation of incidental or consequential damages,
 so this exclusion and limitation may not apply to You.
 .
 9) Acceptance and Termination. If You distribute  copies of the Original
 Work or a Derivative Work, You must make a reasonable effort under the
 circumstances to obtain the express assent of recipients to the terms of
 this License.  Nothing else but this License (or another written agreement
 between Licensor and You) grants You permission to create Derivative Works
 based upon the Original Work or to exercise any of the rights granted
 in Section 1 herein, and any attempt to do so except under the terms of
 this License (or another written agreement between Licensor and You)
 is expressly prohibited by U.S. copyright law, the equivalent laws of
 other countries, and by international treaty.  Therefore, by exercising
 any of the rights granted to You in Section 1 herein, You indicate Your
 acceptance of this License and all of its terms and conditions.
 .
 10) Termination for Patent Action. This License shall terminate
 automatically and You may no longer exercise any of the rights granted
 to You by this License as of the date You commence an action, including a
 cross-claim or counterclaim, for patent infringement (i) against Licensor
 with respect to a patent applicable to software or (ii) against any entity
 with respect to a patent applicable to the Original Work (but excluding
 combinations of the Original Work with other software or hardware).
 .
 11) Jurisdiction, Venue and Governing Law. Any action or suit relating to
 this License may be brought only in the courts of a jurisdiction wherein
 the Licensor resides or in which Licensor conducts its primary business,
 and under the laws of that jurisdiction excluding its conflict-of-law
 provisions.  The application of the United Nations Convention on Contracts
 for the International Sale of Goods is expressly excluded.  Any use of the
 Original Work outside the scope of this License or after its termination
 shall be subject to the requirements and penalties of the U.S. Copyright
 Act, 17 U.S.C. ¤ 101 et seq., the equivalent laws of other countries,
 and international treaty.  This section shall survive the termination
 of this License.
 .
 12) Attorneys Fees. In any action to enforce the terms of this License or
 seeking damages relating thereto, the prevailing party shall be entitled
 to recover its costs and expenses, including, without limitation,
 reasonable attorneys' fees and costs incurred in connection with such
 action, including any appeal of such action.  This section shall survive
 the termination of this License.
 .
 13) Miscellaneous. This License represents the complete agreement
 concerning the subject matter hereof.  If any provision of this License
 is held to be unenforceable, such provision shall be reformed only to
 the extent necessary to make it enforceable.
 .
 14) Definition of "You" in This License. "You" throughout this License,
 whether in upper or lower case, means an individual or a legal entity
 exercising rights under, and complying with all of the terms of, this
 License.  For legal entities, "You" includes any entity that controls,
 is controlled by, or is under common control with you.  For purposes
 of this definition, "control" means (i) the power, direct or indirect,
 to cause the direction or management of such entity, whether by contract
 or otherwise, or (ii) ownership of fifty percent (50%) or more of the
 outstanding shares, or (iii) beneficial ownership of such entity.
 .
 15) Right to Use. You may use the Original Work in all ways not otherwise
 restricted or conditioned by this License or by law, and Licensor promises
 not to interfere with or be responsible for such uses by You.
 .
 This license is Copyright (C) 2003 Lawrence E. Rosen.  All rights
 reserved.  Permission is hereby granted to copy and distribute this
 license without modification.  This license may not be modified without
 the express written permission of its copyright owner.

License: Apache-2.0 with LLVM exception
 See /usr/share/common-licenses/Apache-2.0 on a Debian system for the text
 of the Apache-2.0 license.
 .
 As an exception, if, as a result of your compiling your source code, portions
 of this Software are embedded into an Object form of such source code, you
 may redistribute such embedded portions in such Object form without complying
 with the conditions of Sections 4(a), 4(b) and 4(d) of the License.
 .
 In addition, if you combine or link compiled forms of this Software with
 software that is licensed under the GPLv2 ("Combined Software") and if a
 court of competent jurisdiction determines that the patent provision (Section
 3), the indemnity provision (Section 9) or other Section of the License
 conflicts with the conditions of the GPLv2, you may retroactively and
 prospectively choose to deem waived or otherwise exclude such Section(s) of
 the License, but only in their entirety and only with respect to the Combined
 Software.

License: BSD-2-clause
  Redistribution and use in source and binary forms, with or without
  modification, are permitted provided that the following conditions
  are met:
  1. Redistributions of source code must retain the above copyright
     notice, this list of conditions and the following disclaimer.
  2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.
  .
  THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
  IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
  OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
  NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
  DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
  THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
  THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

License: bzip2-1.0.6
 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions
 are met:
 .
 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 .
 2. The origin of this software must not be misrepresented; you must
    not claim that you wrote the original software.  If you use this
    software in a product, an acknowledgment in the product
    documentation would be appreciated but is not required.
 .
 3. Altered source versions must be plainly marked as such, and must
    not be misrepresented as being the original software.
 .
 4. The name of the author may not be used to endorse or promote
    products derived from this software without specific prior written
    permission.
 .
 THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS
 OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
 DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
 GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
 WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
 NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

License: CC-BY-SA-3.0
 SPDX license expression "CC-BY-SA-3.0": https://spdx.org/licenses/CC-BY-SA-3.0.html
 .
 Creative Commons Legal Code
 .
 Attribution-ShareAlike 3.0 Unported
 .
     CREATIVE COMMONS CORPORATION IS NOT A LAW FIRM AND DOES NOT PROVIDE
     LEGAL SERVICES. DISTRIBUTION OF THIS LICENSE DOES NOT CREATE AN
     ATTORNEY-CLIENT RELATIONSHIP. CREATIVE COMMONS PROVIDES THIS
     INFORMATION ON AN "AS-IS" BASIS. CREATIVE COMMONS MAKES NO WARRANTIES
     REGARDING THE INFORMATION PROVIDED, AND DISCLAIMS LIABILITY FOR
     DAMAGES RESULTING FROM ITS USE.
 .
 License
 .
 THE WORK (AS DEFINED BELOW) IS PROVIDED UNDER THE TERMS OF THIS CREATIVE
 COMMONS PUBLIC LICENSE ("CCPL" OR "LICENSE"). THE WORK IS PROTECTED BY
 COPYRIGHT AND/OR OTHER APPLICABLE LAW. ANY USE OF THE WORK OTHER THAN AS
 AUTHORIZED UNDER THIS LICENSE OR COPYRIGHT LAW IS PROHIBITED.
 .
 BY EXERCISING ANY RIGHTS TO THE WORK PROVIDED HERE, YOU ACCEPT AND AGREE
 TO BE BOUND BY THE TERMS OF THIS LICENSE. TO THE EXTENT THIS LICENSE MAY
 BE CONSIDERED TO BE A CONTRACT, THE LICENSOR GRANTS YOU THE RIGHTS
 CONTAINED HERE IN CONSIDERATION OF YOUR ACCEPTANCE OF SUCH TERMS AND
 CONDITIONS.
 .
 1. Definitions
 .
  a. "Adaptation" means a work based upon the Work, or upon the Work and
     other pre-existing works, such as a translation, adaptation,
     derivative work, arrangement of music or other alterations of a
     literary or artistic work, or phonogram or performance and includes
     cinematographic adaptations or any other form in which the Work may be
     recast, transformed, or adapted including in any form recognizably
     derived from the original, except that a work that constitutes a
     Collection will not be considered an Adaptation for the purpose of
     this License. For the avoidance of doubt, where the Work is a musical
     work, performance or phonogram, the synchronization of the Work in
     timed-relation with a moving image ("synching") will be considered an
     Adaptation for the purpose of this License.
  b. "Collection" means a collection of literary or artistic works, such as
     encyclopedias and anthologies, or performances, phonograms or
     broadcasts, or other works or subject matter other than works listed
     in Section 1(f) below, which, by reason of the selection and
     arrangement of their contents, constitute intellectual creations, in
     which the Work is included in its entirety in unmodified form along
     with one or more other contributions, each constituting separate and
     independent works in themselves, which together are assembled into a
     collective whole. A work that constitutes a Collection will not be
     considered an Adaptation (as defined below) for the purposes of this
     License.
  c. "Creative Commons Compatible License" means a license that is listed
     at https://creativecommons.org/compatiblelicenses that has been
     approved by Creative Commons as being essentially equivalent to this
     License, including, at a minimum, because that license: (i) contains
     terms that have the same purpose, meaning and effect as the License
     Elements of this License; and, (ii) explicitly permits the relicensing
     of adaptations of works made available under that license under this
     License or a Creative Commons jurisdiction license with the same
     License Elements as this License.
  d. "Distribute" means to make available to the public the original and
     copies of the Work or Adaptation, as appropriate, through sale or
     other transfer of ownership.
  e. "License Elements" means the following high-level license attributes
     as selected by Licensor and indicated in the title of this License:
     Attribution, ShareAlike.
  f. "Licensor" means the individual, individuals, entity or entities that
     offer(s) the Work under the terms of this License.
  g. "Original Author" means, in the case of a literary or artistic work,
     the individual, individuals, entity or entities who created the Work
     or if no individual or entity can be identified, the publisher; and in
     addition (i) in the case of a performance the actors, singers,
     musicians, dancers, and other persons who act, sing, deliver, declaim,
     play in, interpret or otherwise perform literary or artistic works or
     expressions of folklore; (ii) in the case of a phonogram the producer
     being the person or legal entity who first fixes the sounds of a
     performance or other sounds; and, (iii) in the case of broadcasts, the
     organization that transmits the broadcast.
  h. "Work" means the literary and/or artistic work offered under the terms
     of this License including without limitation any production in the
     literary, scientific and artistic domain, whatever may be the mode or
     form of its expression including digital form, such as a book,
     pamphlet and other writing; a lecture, address, sermon or other work
     of the same nature; a dramatic or dramatico-musical work; a
     choreographic work or entertainment in dumb show; a musical
     composition with or without words; a cinematographic work to which are
     assimilated works expressed by a process analogous to cinematography;
     a work of drawing, painting, architecture, sculpture, engraving or
     lithography; a photographic work to which are assimilated works
     expressed by a process analogous to photography; a work of applied
     art; an illustration, map, plan, sketch or three-dimensional work
     relative to geography, topography, architecture or science; a
     performance; a broadcast; a phonogram; a compilation of data to the
     extent it is protected as a copyrightable work; or a work performed by
     a variety or circus performer to the extent it is not otherwise
     considered a literary or artistic work.
  i. "You" means an individual or entity exercising rights under this
     License who has not previously violated the terms of this License with
     respect to the Work, or who has received express permission from the
     Licensor to exercise rights under this License despite a previous
     violation.
  j. "Publicly Perform" means to perform public recitations of the Work and
     to communicate to the public those public recitations, by any means or
     process, including by wire or wireless means or public digital
     performances; to make available to the public Works in such a way that
     members of the public may access these Works from a place and at a
     place individually chosen by them; to perform the Work to the public
     by any means or process and the communication to the public of the
     performances of the Work, including by public digital performance; to
     broadcast and rebroadcast the Work by any means including signs,
     sounds or images.
  k. "Reproduce" means to make copies of the Work by any means including
     without limitation by sound or visual recordings and the right of
     fixation and reproducing fixations of the Work, including storage of a
     protected performance or phonogram in digital form or other electronic
     medium.
 .
 2. Fair Dealing Rights. Nothing in this License is intended to reduce,
 limit, or restrict any uses free from copyright or rights arising from
 limitations or exceptions that are provided for in connection with the
 copyright protection under copyright law or other applicable laws.
 .
 3. License Grant. Subject to the terms and conditions of this License,
 Licensor hereby grants You a worldwide, royalty-free, non-exclusive,
 perpetual (for the duration of the applicable copyright) license to
 exercise the rights in the Work as stated below:
 .
  a. to Reproduce the Work, to incorporate the Work into one or more
     Collections, and to Reproduce the Work as incorporated in the
     Collections;
  b. to create and Reproduce Adaptations provided that any such Adaptation,
     including any translation in any medium, takes reasonable steps to
     clearly label, demarcate or otherwise identify that changes were made
     to the original Work. For example, a translation could be marked "The
     original work was translated from English to Spanish," or a
     modification could indicate "The original work has been modified.";
  c. to Distribute and Publicly Perform the Work including as incorporated
     in Collections; and,
  d. to Distribute and Publicly Perform Adaptations.
  e. For the avoidance of doubt:
 .
      i. Non-waivable Compulsory License Schemes. In those jurisdictions in
         which the right to collect royalties through any statutory or
         compulsory licensing scheme cannot be waived, the Licensor
         reserves the exclusive right to collect such royalties for any
         exercise by You of the rights granted under this License;
     ii. Waivable Compulsory License Schemes. In those jurisdictions in
         which the right to collect royalties through any statutory or
         compulsory licensing scheme can be waived, the Licensor waives the
         exclusive right to collect such royalties for any exercise by You
         of the rights granted under this License; and,
    iii. Voluntary License Schemes. The Licensor waives the right to
         collect royalties, whether individually or, in the event that the
         Licensor is a member of a collecting society that administers
         voluntary licensing schemes, via that society, from any exercise
         by You of the rights granted under this License.
 .
 The above rights may be exercised in all media and formats whether now
 known or hereafter devised. The above rights include the right to make
 such modifications as are technically necessary to exercise the rights in
 other media and formats. Subject to Section 8(f), all rights not expressly
 granted by Licensor are hereby reserved.
 .
 4. Restrictions. The license granted in Section 3 above is expressly made
 subject to and limited by the following restrictions:
 .
  a. You may Distribute or Publicly Perform the Work only under the terms
     of this License. You must include a copy of, or the Uniform Resource
     Identifier (URI) for, this License with every copy of the Work You
     Distribute or Publicly Perform. You may not offer or impose any terms
     on the Work that restrict the terms of this License or the ability of
     the recipient of the Work to exercise the rights granted to that
     recipient under the terms of the License. You may not sublicense the
     Work. You must keep intact all notices that refer to this License and
     to the disclaimer of warranties with every copy of the Work You
     Distribute or Publicly Perform. When You Distribute or Publicly
     Perform the Work, You may not impose any effective technological
     measures on the Work that restrict the ability of a recipient of the
     Work from You to exercise the rights granted to that recipient under
     the terms of the License. This Section 4(a) applies to the Work as
     incorporated in a Collection, but this does not require the Collection
     apart from the Work itself to be made subject to the terms of this
     License. If You create a Collection, upon notice from any Licensor You
     must, to the extent practicable, remove from the Collection any credit
     as required by Section 4(c), as requested. If You create an
     Adaptation, upon notice from any Licensor You must, to the extent
     practicable, remove from the Adaptation any credit as required by
     Section 4(c), as requested.
  b. You may Distribute or Publicly Perform an Adaptation only under the
     terms of: (i) this License; (ii) a later version of this License with
     the same License Elements as this License; (iii) a Creative Commons
     jurisdiction license (either this or a later license version) that
     contains the same License Elements as this License (e.g.,
     Attribution-ShareAlike 3.0 US)); (iv) a Creative Commons Compatible
     License. If you license the Adaptation under one of the licenses
     mentioned in (iv), you must comply with the terms of that license. If
     you license the Adaptation under the terms of any of the licenses
     mentioned in (i), (ii) or (iii) (the "Applicable License"), you must
     comply with the terms of the Applicable License generally and the
     following provisions: (I) You must include a copy of, or the URI for,
     the Applicable License with every copy of each Adaptation You
     Distribute or Publicly Perform; (II) You may not offer or impose any
     terms on the Adaptation that restrict the terms of the Applicable
     License or the ability of the recipient of the Adaptation to exercise
     the rights granted to that recipient under the terms of the Applicable
     License; (III) You must keep intact all notices that refer to the
     Applicable License and to the disclaimer of warranties with every copy
     of the Work as included in the Adaptation You Distribute or Publicly
     Perform; (IV) when You Distribute or Publicly Perform the Adaptation,
     You may not impose any effective technological measures on the
     Adaptation that restrict the ability of a recipient of the Adaptation
     from You to exercise the rights granted to that recipient under the
     terms of the Applicable License. This Section 4(b) applies to the
     Adaptation as incorporated in a Collection, but this does not require
     the Collection apart from the Adaptation itself to be made subject to
     the terms of the Applicable License.
  c. If You Distribute, or Publicly Perform the Work or any Adaptations or
     Collections, You must, unless a request has been made pursuant to
     Section 4(a), keep intact all copyright notices for the Work and
     provide, reasonable to the medium or means You are utilizing: (i) the
     name of the Original Author (or pseudonym, if applicable) if supplied,
     and/or if the Original Author and/or Licensor designate another party
     or parties (e.g., a sponsor institute, publishing entity, journal) for
     attribution ("Attribution Parties") in Licensor's copyright notice,
     terms of service or by other reasonable means, the name of such party
     or parties; (ii) the title of the Work if supplied; (iii) to the
     extent reasonably practicable, the URI, if any, that Licensor
     specifies to be associated with the Work, unless such URI does not
     refer to the copyright notice or licensing information for the Work;
     and (iv) , consistent with Ssection 3(b), in the case of an
     Adaptation, a credit identifying the use of the Work in the Adaptation
     (e.g., "French translation of the Work by Original Author," or
     "Screenplay based on original Work by Original Author"). The credit
     required by this Section 4(c) may be implemented in any reasonable
     manner; provided, however, that in the case of a Adaptation or
     Collection, at a minimum such credit will appear, if a credit for all
     contributing authors of the Adaptation or Collection appears, then as
     part of these credits and in a manner at least as prominent as the
     credits for the other contributing authors. For the avoidance of
     doubt, You may only use the credit required by this Section for the
     purpose of attribution in the manner set out above and, by exercising
     Your rights under this License, You may not implicitly or explicitly
     assert or imply any connection with, sponsorship or endorsement by the
     Original Author, Licensor and/or Attribution Parties, as appropriate,
     of You or Your use of the Work, without the separate, express prior
     written permission of the Original Author, Licensor and/or Attribution
     Parties.
  d. Except as otherwise agreed in writing by the Licensor or as may be
     otherwise permitted by applicable law, if You Reproduce, Distribute or
     Publicly Perform the Work either by itself or as part of any
     Adaptations or Collections, You must not distort, mutilate, modify or
     take other derogatory action in relation to the Work which would be
     prejudicial to the Original Author's honor or reputation. Licensor
     agrees that in those jurisdictions (e.g. Japan), in which any exercise
     of the right granted in Section 3(b) of this License (the right to
     make Adaptations) would be deemed to be a distortion, mutilation,
     modification or other derogatory action prejudicial to the Original
     Author's honor and reputation, the Licensor will waive or not assert,
     as appropriate, this Section, to the fullest extent permitted by the
     applicable national law, to enable You to reasonably exercise Your
     right under Section 3(b) of this License (right to make Adaptations)
     but not otherwise.
 .
 5. Representations, Warranties and Disclaimer
 .
 UNLESS OTHERWISE MUTUALLY AGREED TO BY THE PARTIES IN WRITING, LICENSOR
 OFFERS THE WORK AS-IS AND MAKES NO REPRESENTATIONS OR WARRANTIES OF ANY
 KIND CONCERNING THE WORK, EXPRESS, IMPLIED, STATUTORY OR OTHERWISE,
 INCLUDING, WITHOUT LIMITATION, WARRANTIES OF TITLE, MERCHANTIBILITY,
 FITNESS FOR A PARTICULAR PURPOSE, NONINFRINGEMENT, OR THE ABSENCE OF
 LATENT OR OTHER DEFECTS, ACCURACY, OR THE PRESENCE OF ABSENCE OF ERRORS,
 WHETHER OR NOT DISCOVERABLE. SOME JURISDICTIONS DO NOT ALLOW THE EXCLUSION
 OF IMPLIED WARRANTIES, SO SUCH EXCLUSION MAY NOT APPLY TO YOU.
 .
 6. Limitation on Liability. EXCEPT TO THE EXTENT REQUIRED BY APPLICABLE
 LAW, IN NO EVENT WILL LICENSOR BE LIABLE TO YOU ON ANY LEGAL THEORY FOR
 ANY SPECIAL, INCIDENTAL, CONSEQUENTIAL, PUNITIVE OR EXEMPLARY DAMAGES
 ARISING OUT OF THIS LICENSE OR THE USE OF THE WORK, EVEN IF LICENSOR HAS
 BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.
 .
 7. Termination
 .
  a. This License and the rights granted hereunder will terminate
     automatically upon any breach by You of the terms of this License.
     Individuals or entities who have received Adaptations or Collections
     from You under this License, however, will not have their licenses
     terminated provided such individuals or entities remain in full
     compliance with those licenses. Sections 1, 2, 5, 6, 7, and 8 will
     survive any termination of this License.
  b. Subject to the above terms and conditions, the license granted here is
     perpetual (for the duration of the applicable copyright in the Work).
     Notwithstanding the above, Licensor reserves the right to release the
     Work under different license terms or to stop distributing the Work at
     any time; provided, however that any such election will not serve to
     withdraw this License (or any other license that has been, or is
     required to be, granted under the terms of this License), and this
     License will continue in full force and effect unless terminated as
     stated above.
 .
 8. Miscellaneous
 .
  a. Each time You Distribute or Publicly Perform the Work or a Collection,
     the Licensor offers to the recipient a license to the Work on the same
     terms and conditions as the license granted to You under this License.
  b. Each time You Distribute or Publicly Perform an Adaptation, Licensor
     offers to the recipient a license to the original Work on the same
     terms and conditions as the license granted to You under this License.
  c. If any provision of this License is invalid or unenforceable under
     applicable law, it shall not affect the validity or enforceability of
     the remainder of the terms of this License, and without further action
     by the parties to this agreement, such provision shall be reformed to
     the minimum extent necessary to make such provision valid and
     enforceable.
  d. No term or provision of this License shall be deemed waived and no
     breach consented to unless such waiver or consent shall be in writing
     and signed by the party to be charged with such waiver or consent.
  e. This License constitutes the entire agreement between the parties with
     respect to the Work licensed here. There are no understandings,
     agreements or representations with respect to the Work not specified
     here. Licensor shall not be bound by any additional provisions that
     may appear in any communication from You. This License may not be
     modified without the mutual written agreement of the Licensor and You.
  f. The rights granted under, and the subject matter referenced, in this
     License were drafted utilizing the terminology of the Berne Convention
     for the Protection of Literary and Artistic Works (as amended on
     September 28, 1979), the Rome Convention of 1961, the WIPO Copyright
     Treaty of 1996, the WIPO Performances and Phonograms Treaty of 1996
     and the Universal Copyright Convention (as revised on July 24, 1971).
     These rights and subject matter take effect in the relevant
     jurisdiction in which the License terms are sought to be enforced
     according to the corresponding provisions of the implementation of
     those treaty provisions in the applicable national law. If the
     standard suite of rights granted under applicable copyright law
     includes additional rights not granted under this License, such
     additional rights are deemed to be included in the License; this
     License is not intended to restrict the license of any rights under
     applicable law.
 .
 .
 Creative Commons Notice
 .
     Creative Commons is not a party to this License, and makes no warranty
     whatsoever in connection with the Work. Creative Commons will not be
     liable to You or any party on any legal theory for any damages
     whatsoever, including without limitation any general, special,
     incidental or consequential damages arising in connection to this
     license. Notwithstanding the foregoing two (2) sentences, if Creative
     Commons has expressly identified itself as the Licensor hereunder, it
     shall have all rights and obligations of Licensor.
 .
     Except for the limited purpose of indicating to the public that the
     Work is licensed under the CCPL, Creative Commons does not authorize
     the use by either party of the trademark "Creative Commons" or any
     related trademark or logo of Creative Commons without the prior
     written consent of Creative Commons. Any permitted use will be in
     compliance with Creative Commons' then-current trademark usage
     guidelines, as may be published on its website or otherwise made
     available upon request from time to time. For the avoidance of doubt,
     this trademark restriction does not form part of the License.
 .
     Creative Commons may be contacted at https://creativecommons.org/.

License: CC0-1.0
 SPDX license expression "CC0-1.0": https://spdx.org/licenses/CC0-1.0.html
 On Debian systems, the complete text of the CC0 Public Domain Dedication
 can be found in "/usr/share/common-licenses/CC0-1.0".

License: Expat
 SPDX license expression "MIT": https://spdx.org/licenses/MIT.html
 .
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in
 all copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 THE SOFTWARE.

License: FSFAP
 This file is free software; the author(s) gives unlimited
 permission to copy and/or distribute it, with or without
 modifications, as long as this notice is preserved.

License: FSFULLR
 This file is free software; the Free Software Foundation
 gives unlimited permission to copy and/or distribute it,
 with or without modifications, as long as this notice is preserved.

License: GPL-2+
 This program is free software; you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation; either version 2, or (at your option)
 any later version.
 .
 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.
 .
 On Debian systems, a copy of the GPL version 2 can be found in
 /usr/share/common-licenses/GPL-2.

License: GPL with Autoconf exception
 This file is free software, distributed under the terms of the GNU
 General Public License.  As a special exception to the GNU General
 Public License, this file may be distributed as part of a program
 that contains a configuration script generated by Autoconf, under
 the same distribution terms as the rest of that program.

License: Kuchling-PD
 Distribute and use freely; there are no restrictions on further
 dissemination and usage except those imposed by the laws of your
 country of residence.

License: LGPL-2
 Code imported from cmph project, which describes it as "LGPL-2 and MPL 1.1"
 On Debian systems, a copy of the LGPL version 2 can be found in
 /usr/share/common-licenses/LGPL-2.

License: LGPL-2+
 The GNU CHARSET Library is free software; you can redistribute it and/or
 modify it under the terms of the GNU Library General Public License as
 published by the Free Software Foundation; either version 2 of the
 License, or (at your option) any later version.
 .
 The GNU CHARSET Library is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 Library General Public License for more details.
 .
 On Debian systems, a copy of the LGPL version 2 can be found in
 /usr/share/common-licenses/LGPL-2.

License: LGPL-2.1+
 This library is free software; you can redistribute it and/or
 modify it under the terms of the GNU Lesser General Public
 License as published by the Free Software Foundation; either
 version 2.1 of the License, or (at your option) any later version.
 .
 This library is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 Lesser General Public License for more details.
 .
 On Debian systems, a copy of the LGPL version 2.1 can be found in
 /usr/share/common-licenses/LGPL-2.1.

License: LGPL-3+
 This program is free software; you can redistribute it and/or modify it under
 the terms of the GNU Lesser General Public License as published by the Free
 Software Foundation; either version 3 of the License, or (at your option) any
 later version.  See http://www.gnu.org/copyleft/lgpl.html for the full text
 of the license.
 .
 On Debian systems, a copy of the LGPL version 3 can be found in
 /usr/share/common-licenses/LGPL-3.

License: MPL-1.1
 Code imported from cmph project, which describes it as "LGPL-2 and MPL 1.1"
 On Debian systems, a copy of the MPL version 1.1 can be found in
 /usr/share/common-licenses/MPL-1.1.

License: Plumb-PD
 The algorithm is due to Ron Rivest.  This code was
 written by Colin Plumb in 1993, no copyright is claimed.
 This code is in the public domain; do with it what you wish.

License: Unicode-DFS-2016
 See Terms of Use <https://www.unicode.org/copyright.html>
 for definitions of Unicode Inc.’s Data Files and Software.
 .
 NOTICE TO USER: Carefully read the following legal agreement.
 BY DOWNLOADING, INSTALLING, COPYING OR OTHERWISE USING UNICODE INC.'S
 DATA FILES ("DATA FILES"), AND/OR SOFTWARE ("SOFTWARE"),
 YOU UNEQUIVOCALLY ACCEPT, AND AGREE TO BE BOUND BY, ALL OF THE
 TERMS AND CONDITIONS OF THIS AGREEMENT.
 IF YOU DO NOT AGREE, DO NOT DOWNLOAD, INSTALL, COPY, DISTRIBUTE OR USE
 THE DATA FILES OR SOFTWARE.
 .
 COPYRIGHT AND PERMISSION NOTICE
 .
 Copyright © 1991-2022 Unicode, Inc. All rights reserved.
 Distributed under the Terms of Use in https://www.unicode.org/copyright.html.
 .
 Permission is hereby granted, free of charge, to any person obtaining
 a copy of the Unicode data files and any associated documentation
 (the "Data Files") or Unicode software and any associated documentation
 (the "Software") to deal in the Data Files or Software
 without restriction, including without limitation the rights to use,
 copy, modify, merge, publish, distribute, and/or sell copies of
 the Data Files or Software, and to permit persons to whom the Data Files
 or Software are furnished to do so, provided that either
 (a) this copyright and permission notice appear with all copies
 of the Data Files or Software, or
 (b) this copyright and permission notice appear in associated
 Documentation.
 .
 THE DATA FILES AND SOFTWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF
 ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
 WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
 NONINFRINGEMENT OF THIRD PARTY RIGHTS.
```

---

## gstreamer1.0-python3-plugin-loader:1.24.1-1

**License Type:** LGPL-2.1+ / LGPL-2+

**Source:** Debian/APT

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: GStreamer GObject Introspection overrides for python 1.0
Upstream-Contact: gstreamer-devel@lists.freedesktop.org
Source: https://gstreamer.freedesktop.org
Comment: This package was debianized by Sebastian Dröge <slomo@debian.org> on
 Tue, 30 Sep 2013 11:59:10 +0200.

Files: gi/overrides/GstPbutils.py
Copyright: 2012, Alessandro Decina <alessandro.d@gmail.com>
License: LGPL-2.1+
 /usr/share/common-licenses/LGPL-2.1

Files: gi/overrides/Gst.py
Copyright: 2012, Thibault Saunier <thibault.saunier@collabora.com>
License: LGPL-2.1+
 /usr/share/common-licenses/LGPL-2.1

Files: gi/overrides/gstmodule.c
Copyright: 2002, David I. Lehn
  2012, Thibault Saunier <thibault.saunier@collabora.com>
License: LGPL-2+
 /usr/share/common-licenses/LGPL-2
```

---

## libeigen3-dev:3.4.0-4build0.1

**License Type:** MPL-2 / LGPL-2.1+ or MPL-2 / BSD-3-clause / LGPL-2.1+

**Source:** Debian/APT

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Source: http://eigen.tuxfamily.org

Files: *
Copyright: 2006-2013 Benoit Jacob <jacob@math.jussieu.fr>
           2008-2013 Gael Guennebaud <g.gael@free.fr>
           2007 Michael Olbrich <michael.olbrich@gmx.net>
           2008 Konstantinos Margaritis <markos@codex.gr>
License: MPL-2

Files: unsupported/test/mpreal/* unsupported/Eigen/src/IterativeSolvers/* Eigen/src/SparseCholesky/SimplicialCholesky.h Eigen/src/OrderingMethods/Amd.h
Copyright: 2002-2007 Yves Renard
           2008-2009 Gael Guennebaud <g.gael@free.fr>
License: LGPL-2.1+ or MPL-2

Files: Eigen/src/Cholesky/LLT_LAPACKE.h Eigen/src/QR/HouseholderQR_LAPACKE.h Eigen/src/QR/ColPivHouseholderQR_LAPACKE.h Eigen/src/LU/arch/Inverse_SSE.h Eigen/src/LU/PartialPivLU_LAPACKE.h Eigen/src/Eigenvalues/ComplexSchur_LAPACKE.h Eigen/src/Eigenvalues/SelfAdjointEigenSolver_LAPACKE.h Eigen/src/Eigenvalues/RealSchur_LAPACKE.h Eigen/src/PardisoSupport/PardisoSupport.h Eigen/src/misc/lapacke.h Eigen/src/SVD/JacobiSVD_LAPACKE.h Eigen/src/Core/Assign_MKL.h Eigen/src/Core/products/GeneralMatrixMatrixTriangular_BLAS.h Eigen/src/Core/products/TriangularMatrixMatrix_BLAS.h Eigen/src/Core/products/TriangularMatrixVector_BLAS.h Eigen/src/Core/products/SelfadjointMatrixVector_BLAS.h Eigen/src/Core/products/TriangularSolverMatrix_BLAS.h Eigen/src/Core/products/GeneralMatrixVector_BLAS.h Eigen/src/Core/products/GeneralMatrixMatrix_BLAS.h Eigen/src/Core/products/SelfadjointMatrixMatrix_BLAS.h Eigen/src/Core/util/MKL_support.h doc/UsingBlasLapackBackends.dox doc/UsingIntelMKL.dox
Copyright: 2011 Intel Corporation
License: BSD-3-clause

License: BSD-3-clause
    Redistribution and use in source and binary forms, with or without modification,
     are permitted provided that the following conditions are met:
 .
     * Redistributions of source code must retain the above copyright notice, this
       list of conditions and the following disclaimer.
     * Redistributions in binary form must reproduce the above copyright notice,
       this list of conditions and the following disclaimer in the documentation
       and/or other materials provided with the distribution.
     * Neither the name of Intel Corporation nor the names of its contributors may
       be used to endorse or promote products derived from this software without
       specific prior written permission.
 .
     THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
     ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
     WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
     DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
     ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
     (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
     LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
     ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
     (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
     SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

License: LGPL-2.1+
    On Debian systems, the complete text of the GNU Lesser General
    Public License can be found in `/usr/share/common-licenses/LGPL-2.1'.

License: MPL-2
    Mozilla Public License Version 2.0
    ==================================
 .
    1. Definitions
    --------------
 .
    1.1. "Contributor"
    means each individual or legal entity that creates, contributes to
    the creation of, or owns Covered Software.
 .
    1.2. "Contributor Version"
    means the combination of the Contributions of others (if any) used
    by a Contributor and that particular Contributor's Contribution.
 .
    1.3. "Contribution"
    means Covered Software of a particular Contributor.
 .
    1.4. "Covered Software"
    means Source Code Form to which the initial Contributor has attached
    the notice in Exhibit A, the Executable Form of such Source Code
    Form, and Modifications of such Source Code Form, in each case
    including portions thereof.
 .
    1.5. "Incompatible With Secondary Licenses"
    means
 .
    (a) that the initial Contributor has attached the notice described
        in Exhibit B to the Covered Software; or
 .
    (b) that the Covered Software was made available under the terms of
        version 1.1 or earlier of the License, but not also under the
        terms of a Secondary License.
 .
    1.6. "Executable Form"
    means any form of the work other than Source Code Form.
 .
    1.7. "Larger Work"
    means a work that combines Covered Software with other material, in
    a separate file or files, that is not Covered Software.
 .
    1.8. "License"
    means this document.
 .
    1.9. "Licensable"
    means having the right to grant, to the maximum extent possible,
    whether at the time of the initial grant or subsequently, any and
    all of the rights conveyed by this License.
 .
    1.10. "Modifications"
    means any of the following:
 .
    (a) any file in Source Code Form that results from an addition to,
        deletion from, or modification of the contents of Covered
        Software; or
 .
    (b) any new file in Source Code Form that contains any Covered
        Software.
 .
    1.11. "Patent Claims" of a Contributor
    means any patent claim(s), including without limitation, method,
    process, and apparatus claims, in any patent Licensable by such
    Contributor that would be infringed, but for the grant of the
    License, by the making, using, selling, offering for sale, having
    made, import, or transfer of either its Contributions or its
    Contributor Version.
 .
    1.12. "Secondary License"
    means either the GNU General Public License, Version 2.0, the GNU
    Lesser General Public License, Version 2.1, the GNU Affero General
    Public License, Version 3.0, or any later versions of those
    licenses.
 .
    1.13. "Source Code Form"
    means the form of the work preferred for making modifications.
 .
    1.14. "You" (or "Your")
    means an individual or a legal entity exercising rights under this
    License. For legal entities, "You" includes any entity that
    controls, is controlled by, or is under common control with You. For
    purposes of this definition, "control" means (a) the power, direct
    or indirect, to cause the direction or management of such entity,
    whether by contract or otherwise, or (b) ownership of more than
    fifty percent (50%) of the outstanding shares or beneficial
    ownership of such entity.
 .
    2. License Grants and Conditions
    --------------------------------
 .
    2.1. Grants
 .
    Each Contributor hereby grants You a world-wide, royalty-free,
    non-exclusive license:
 .
    (a) under intellectual property rights (other than patent or trademark)
    Licensable by such Contributor to use, reproduce, make available,
    modify, display, perform, distribute, and otherwise exploit its
    Contributions, either on an unmodified basis, with Modifications, or
    as part of a Larger Work; and
 .
    (b) under Patent Claims of such Contributor to make, use, sell, offer
    for sale, have made, import, and otherwise transfer either its
    Contributions or its Contributor Version.
 .
    2.2. Effective Date
 .
    The licenses granted in Section 2.1 with respect to any Contribution
    become effective for each Contribution on the date the Contributor first
    distributes such Contribution.
 .
    2.3. Limitations on Grant Scope
 .
    The licenses granted in this Section 2 are the only rights granted under
    this License. No additional rights or licenses will be implied from the
    distribution or licensing of Covered Software under this License.
    Notwithstanding Section 2.1(b) above, no patent license is granted by a
    Contributor:
 .
    (a) for any code that a Contributor has removed from Covered Software;
    or
 .
    (b) for infringements caused by: (i) Your and any other third party's
    modifications of Covered Software, or (ii) the combination of its
    Contributions with other software (except as part of its Contributor
    Version); or
 .
    (c) under Patent Claims infringed by Covered Software in the absence of
    its Contributions.
 .
    This License does not grant any rights in the trademarks, service marks,
    or logos of any Contributor (except as may be necessary to comply with
    the notice requirements in Section 3.4).
 .
    2.4. Subsequent Licenses
 .
    No Contributor makes additional grants as a result of Your choice to
    distribute the Covered Software under a subsequent version of this
    License (see Section 10.2) or under the terms of a Secondary License (if
    permitted under the terms of Section 3.3).
 .
    2.5. Representation
 .
    Each Contributor represents that the Contributor believes its
    Contributions are its original creation(s) or it has sufficient rights
    to grant the rights to its Contributions conveyed by this License.
 .
    2.6. Fair Use
 .
    This License is not intended to limit any rights You have under
    applicable copyright doctrines of fair use, fair dealing, or other
    equivalents.
 .
    2.7. Conditions
 .
    Sections 3.1, 3.2, 3.3, and 3.4 are conditions of the licenses granted
    in Section 2.1.
 .
    3. Responsibilities
    -------------------
 .
    3.1. Distribution of Source Form
 .
    All distribution of Covered Software in Source Code Form, including any
    Modifications that You create or to which You contribute, must be under
    the terms of this License. You must inform recipients that the Source
    Code Form of the Covered Software is governed by the terms of this
    License, and how they can obtain a copy of this License. You may not
    attempt to alter or restrict the recipients' rights in the Source Code
    Form.
 .
    3.2. Distribution of Executable Form
 .
    If You distribute Covered Software in Executable Form then:
 .
    (a) such Covered Software must also be made available in Source Code
    Form, as described in Section 3.1, and You must inform recipients of
    the Executable Form how they can obtain a copy of such Source Code
    Form by reasonable means in a timely manner, at a charge no more
    than the cost of distribution to the recipient; and
 .
    (b) You may distribute such Executable Form under the terms of this
    License, or sublicense it under different terms, provided that the
    license for the Executable Form does not attempt to limit or alter
    the recipients' rights in the Source Code Form under this License.
 .
    3.3. Distribution of a Larger Work
 .
    You may create and distribute a Larger Work under terms of Your choice,
    provided that You also comply with the requirements of this License for
    the Covered Software. If the Larger Work is a combination of Covered
    Software with a work governed by one or more Secondary Licenses, and the
    Covered Software is not Incompatible With Secondary Licenses, this
    License permits You to additionally distribute such Covered Software
    under the terms of such Secondary License(s), so that the recipient of
    the Larger Work may, at their option, further distribute the Covered
    Software under the terms of either this License or such Secondary
    License(s).
 .
    3.4. Notices
 .
    You may not remove or alter the substance of any license notices
    (including copyright notices, patent notices, disclaimers of warranty,
    or limitations of liability) contained within the Source Code Form of
    the Covered Software, except that You may alter any license notices to
    the extent required to remedy known factual inaccuracies.
 .
    3.5. Application of Additional Terms
 .
    You may choose to offer, and to charge a fee for, warranty, support,
    indemnity or liability obligations to one or more recipients of Covered
    Software. However, You may do so only on Your own behalf, and not on
    behalf of any Contributor. You must make it absolutely clear that any
    such warranty, support, indemnity, or liability obligation is offered by
    You alone, and You hereby agree to indemnify every Contributor for any
    liability incurred by such Contributor as a result of warranty, support,
    indemnity or liability terms You offer. You may include additional
    disclaimers of warranty and limitations of liability specific to any
    jurisdiction.
 .
    4. Inability to Comply Due to Statute or Regulation
    ---------------------------------------------------
 .
    If it is impossible for You to comply with any of the terms of this
    License with respect to some or all of the Covered Software due to
    statute, judicial order, or regulation then You must: (a) comply with
    the terms of this License to the maximum extent possible; and (b)
    describe the limitations and the code they affect. Such description must
    be placed in a text file included with all distributions of the Covered
    Software under this License. Except to the extent prohibited by statute
    or regulation, such description must be sufficiently detailed for a
    recipient of ordinary skill to be able to understand it.
 .
    5. Termination
    --------------
 .
    5.1. The rights granted under this License will terminate automatically
    if You fail to comply with any of its terms. However, if You become
    compliant, then the rights granted under this License from a particular
    Contributor are reinstated (a) provisionally, unless and until such
    Contributor explicitly and finally terminates Your grants, and (b) on an
    ongoing basis, if such Contributor fails to notify You of the
    non-compliance by some reasonable means prior to 60 days after You have
    come back into compliance. Moreover, Your grants from a particular
    Contributor are reinstated on an ongoing basis if such Contributor
    notifies You of the non-compliance by some reasonable means, this is the
    first time You have received notice of non-compliance with this License
    from such Contributor, and You become compliant prior to 30 days after
    Your receipt of the notice.
 .
    5.2. If You initiate litigation against any entity by asserting a patent
    infringement claim (excluding declaratory judgment actions,
    counter-claims, and cross-claims) alleging that a Contributor Version
    directly or indirectly infringes any patent, then the rights granted to
    You by any and all Contributors for the Covered Software under Section
    2.1 of this License shall terminate.
 .
    5.3. In the event of termination under Sections 5.1 or 5.2 above, all
    end user license agreements (excluding distributors and resellers) which
    have been validly granted by You or Your distributors under this License
    prior to termination shall survive termination.
 .
    ************************************************************************
    *                                                                      *
    *  6. Disclaimer of Warranty                                           *
    *  -------------------------                                           *
    *                                                                      *
    *  Covered Software is provided under this License on an "as is"       *
    *  basis, without warranty of any kind, either expressed, implied, or  *
    *  statutory, including, without limitation, warranties that the       *
    *  Covered Software is free of defects, merchantable, fit for a        *
    *  particular purpose or non-infringing. The entire risk as to the     *
    *  quality and performance of the Covered Software is with You.        *
    *  Should any Covered Software prove defective in any respect, You     *
    *  (not any Contributor) assume the cost of any necessary servicing,   *
    *  repair, or correction. This disclaimer of warranty constitutes an   *
    *  essential part of this License. No use of any Covered Software is   *
    *  authorized under this License except under this disclaimer.         *
    *                                                                      *
    ************************************************************************
 .
    ************************************************************************
    *                                                                      *
    *  7. Limitation of Liability                                          *
    *  --------------------------                                          *
    *                                                                      *
    *  Under no circumstances and under no legal theory, whether tort      *
    *  (including negligence), contract, or otherwise, shall any           *
    *  Contributor, or anyone who distributes Covered Software as          *
    *  permitted above, be liable to You for any direct, indirect,         *
    *  special, incidental, or consequential damages of any character      *
    *  including, without limitation, damages for lost profits, loss of    *
    *  goodwill, work stoppage, computer failure or malfunction, or any    *
    *  and all other commercial damages or losses, even if such party      *
    *  shall have been informed of the possibility of such damages. This   *
    *  limitation of liability shall not apply to liability for death or   *
    *  personal injury resulting from such party's negligence to the       *
    *  extent applicable law prohibits such limitation. Some               *
    *  jurisdictions do not allow the exclusion or limitation of           *
    *  incidental or consequential damages, so this exclusion and          *
    *  limitation may not apply to You.                                    *
    *                                                                      *
    ************************************************************************
 .
    8. Litigation
    -------------
 .
    Any litigation relating to this License may be brought only in the
    courts of a jurisdiction where the defendant maintains its principal
    place of business and such litigation shall be governed by laws of that
    jurisdiction, without reference to its conflict-of-law provisions.
    Nothing in this Section shall prevent a party's ability to bring
    cross-claims or counter-claims.
 .
    9. Miscellaneous
    ----------------
 .
    This License represents the complete agreement concerning the subject
    matter hereof. If any provision of this License is held to be
    unenforceable, such provision shall be reformed only to the extent
    necessary to make it enforceable. Any law or regulation which provides
    that the language of a contract shall be construed against the drafter
    shall not be used to construe this License against a Contributor.
 .
    10. Versions of the License
    ---------------------------
 .
    10.1. New Versions
 .
    Mozilla Foundation is the license steward. Except as provided in Section
    10.3, no one other than the license steward has the right to modify or
    publish new versions of this License. Each version will be given a
    distinguishing version number.
 .
    10.2. Effect of New Versions
 .
    You may distribute the Covered Software under the terms of the version
    of the License under which You originally received the Covered Software,
    or under the terms of any subsequent version published by the license
    steward.
 .
    10.3. Modified Versions
 .
    If you create software not governed by this License, and you want to
    create a new license for such software, you may create and use a
    modified version of this License if you rename the license and remove
    any references to the name of the license steward (except to note that
    such modified license differs from this License).
 .
    10.4. Distributing Source Code Form that is Incompatible With Secondary
    Licenses
 .
    If You choose to distribute Source Code Form that is Incompatible With
    Secondary Licenses under the terms of this version of the License, the
    notice described in Exhibit B of this License must be attached.
 .
    Exhibit A - Source Code Form License Notice
    -------------------------------------------
 .
    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.
 .
    If it is not possible or desirable to put the notice in a particular
    file, then You may include the notice in a location (such as a LICENSE
    file in a relevant directory) where a recipient would be likely to look
    for such a notice.
 .
    You may add additional accurate notices of copyright ownership.
 .
    Exhibit B - "Incompatible With Secondary Licenses" Notice
    ---------------------------------------------------------
 .
    This Source Code Form is "Incompatible With Secondary Licenses", as
    defined by the Mozilla Public License, v. 2.0.
```

---

## libflac++10:1.4.3+ds-2.1ubuntu2

**License Type:** GPL-2+ or LGPL-2.1+ / GFDL-1.1+ / GPL-2+ / BSD-3-clause / LGPL-2.1+ / LGPL-2+ / Public-domain / ISC

**Source:** Debian/APT

```
--- NVIDIA LICENSE ELECTION NOTE ---
Ships only libFLAC++.so; its implementation is dual-licensed "GPL-2+ OR LGPL-2.1+".
NVIDIA ELECTS LGPL-2.1+, used via dynamic linking only (LGPL-2.1 sec.6 satisfied).
Public FLAC++ API headers and upstream Xiph.org are BSD-3-Clause. The GPL-2+ flac/
metaflac tools, examples, and GFDL docs are NOT in this package; no GPL terms attach.
------------------------------------

Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: FLAC
Upstream-Contact: https://www.xiph.org/flac/
Source: https://git.xiph.org/?p=flac.git
Files-Excluded:
 doc/api/*
 doc/FLAC.tag
 man/*.1

Files: *
Copyright: 2011-2022, Xiph.Org Foundation
License: GPL-2+ or LGPL-2.1+

Files: doc/*
Copyright: 2000-2009, Josh Coalson
  2011-2022, Xiph.Org Foundation
License: GFDL-1.1+
 <!-- Permission is granted to copy, distribute and/or modify this document -->
 <!-- under the terms of the GNU Free Documentation License, Version 1.1 -->
 <!-- or any later version published by the Free Software Foundation; -->
 <!-- with no invariant sections. -->
 .
 On Debian systems, the complete text of the GNU Free Documentation
 License version 1.2 can be found in "/usr/share/common-licenses/GFDL-1.2".

Files: examples/*
 include/share/grabbag/picture.h
 include/test_libs_common/*
 src/flac/*
 src/metaflac/*
 src/share/grabbag/picture.c
 src/share/utf8/*
 src/test_grabbag/*
 src/test_libFLAC++/*
 src/test_libFLAC/*
 src/test_libs_common/*
 src/test_seeking/*
 src/test_streams/*
 src/utils/flacdiff/*
 src/utils/flactimer/*
Copyright: 1994-2013, Free Software Foundation, Inc
  1998-2000, Peter Alm, Mikael Alm, Olle Hallnas, Thomas Nilsson and 4Front Technologies
  1999-2001, Håvard Kvålen <havardk@xmms.org>
  2000-2001, Robert Leslie
  2000-2002, Jerome Couderc <j.couderc@ifrance.com>
  2000-2009, Josh Coalson
  2001, Edmund Grimley Evans <edmundo@rano.org>
  2001, Peter Harris <peter.harris@hummingbird.com>
  2002-2009, Daisuke Shimamura
  2003, Philip Jägenstedt
  2011-2022, Xiph.Org Foundation
License: GPL-2+

Files: include/FLAC++/*
 include/FLAC/*
 include/share/alloc.h
 include/share/compat.h
 include/share/endswap.h
 include/share/macros.h
 include/share/private.h
 include/share/safe_str.h
 include/share/win_utf8_io.h
 src/libFLAC++/*
 src/libFLAC/*
 src/share/grabbag/alloc.c
 src/share/grabbag/snprintf.c
 src/share/win_utf8_io/*
Copyright: 1994-2013, Free Software Foundation, Inc
  2000-2009, Josh Coalson
  2011-2022, Xiph.Org Foundation
License: BSD-3-clause
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *
 * - Redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer.
 *
 * - Redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in the
 * documentation and/or other materials provided with the distribution.
 *
 * - Neither the name of the Xiph.org Foundation nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE FOUNDATION OR
 * CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
 * EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
 * PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
 * PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
 * LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
 * NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

Files: include/share/grabbag.h
 include/share/grabbag/cuesheet.h
 include/share/grabbag/file.h
 include/share/grabbag/replaygain.h
 include/share/grabbag/seektable.h
 include/share/replaygain_analysis.h
 include/share/replaygain_synthesis.h
 src/share/grabbag/cuesheet.c
 src/share/grabbag/file.c
 src/share/grabbag/replaygain.c
 src/share/grabbag/seektable.c
 src/share/replaygain_analysis/*
 src/share/replaygain_synthesis/*
Copyright: 1994-2013, Free Software Foundation, Inc
  2001, David Robinson and Glen Sawyer
  2002, John Edwards
  2002-2009, Josh Coalson
  2011-2022, Xiph.Org Foundation
License: LGPL-2.1+

Files: include/share/getopt.h
 src/share/getopt/getopt.c
 src/share/getopt/getopt1.c
Copyright: 1987-1998 Free Software Foundation, Inc
License: LGPL-2+
   The GNU C Library is free software; you can redistribute it and/or
   modify it under the terms of the GNU Library General Public License as
   published by the Free Software Foundation; either version 2 of the
   License, or (at your option) any later version.
 .
   The GNU C Library is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
   Library General Public License for more details.
 .
 On Debian systems, the complete text of the GNU Lesser General
 Public License can be found in "/usr/share/common-licenses/LGPL-2".

Files: src/libFLAC/include/private/md5.h
 src/libFLAC/md5.c
Copyright: *No copyright*
License: Public-domain
 * This is the header file for the MD5 message-digest algorithm.
 * The algorithm is due to Ron Rivest.  This code was
 * written by Colin Plumb in 1993, no copyright is claimed.
 * This code is in the public domain; do with it what you wish.
 *
 * Equivalent code is available from RSA Data Security, Inc.
 * This code has been tested against that, and is equivalent,
 * except that you don't need to include two pages of legalese
 * with every copy.
 *
 * To compute the message digest of a chunk of bytes, declare an
 * MD5Context structure, pass it to MD5Init, call MD5Update as
 * needed on buffers full of bytes, and then call MD5Final, which
 * will fill a supplied 16-byte array with the digest.
 *
 * Changed so as no longer to depend on Colin Plumb's `usual.h'
 * header definitions; now uses stuff from dpkg's config.h
 *  - Ian Jackson <ijackson@nyx.cs.du.edu>.
 * Still in the public domain.
 *
 * Josh Coalson: made some changes to integrate with libFLAC.
 * Still in the public domain, with no warranty.

Files: src/flac/local_string_utils.c
Copyright: 1998, Todd C. Miller <Todd.Miller@courtesan.com>
License: ISC
 * Permission to use, copy, modify, and distribute this software for any
 * purpose with or without fee is hereby granted, provided that the above
 * copyright notice and this permission notice appear in all copies.
 *
 * THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
 * WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
 * MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
 * ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
 * WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
 * ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
 * OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

Files: debian/*
Copyright: 2001-2005, Matt Zimmerman <mdz@debian.org>
  2005-2007, Joshua Kwan <joshk@triplehelix.org>
  2009-2018, Fabian Greffrath <fabian@debian.org>
License: GPL-2+

License: GPL-2+
 This package is free software; you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation; either version 2 of the License, or
 (at your option) any later version.
 .
 This package is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.
 .
 You should have received a copy of the GNU General Public License
 along with this program. If not, see <http://www.gnu.org/licenses/>
 .
 The complete text of the GNU General Public License version 2
 can be found in "/usr/share/common-licenses/GPL-2".

License: LGPL-2.1+
 This library is free software; you can redistribute it and/or
 modify it under the terms of the GNU Lesser General Public
 License as published by the Free Software Foundation; either
 version 2.1 of the License, or (at your option) any later version.
 .
 This library is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 Lesser General Public License for more details.
 .
 The complete text of the GNU Lesser General Public License
 can be found in "/usr/share/common-licenses/LGPL-2.1".
```

---

## libgirepository-1.0-1:1.80.1-1

**License Type:** GPL-2+ / LGPL-2+ / LGPL-2 or MPL-1.1 / LGPL-2.1+ / BSD-2-clause / FSFAP and FSFULLR / Expat and GPL-2+ / LGPL-2+ and LGPL-2.1+ and FSFULLR and CC0-1.0 / AFL-2.0 or LGPL-2.1+ / Unicode-DFS-2016 / Expat / LGPL-3+ / Apache-2.0 with LLVM exception / LGPL-2.1+ and Kuchling-PD and Plumb-PD / bzip2-1.0.6 / CC-BY-SA-3.0 / GPL with Autoconf exception / AFL-2.0 / CC0-1.0 / FSFAP / FSFULLR / Kuchling-PD / LGPL-2 / MPL-1.1 / Plumb-PD

**Source:** Debian/APT

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Source: https://download.gnome.org/sources/gobject-introspection/
Upstream-Name: GObject Introspection

Files: *
Copyright:
 2012 Canonical Ltd
 2012-2015 Dieter Verfaillie
 2005-2008 Divmod, Inc.
 2008-2011 Johan Dahlin
 2006 Johann C. Rocholl
 2007-2008 Jürg Billeter
 2005 Matthias Clasen
 2008 Philip Van Hoof
 2008-2010 Red Hat, Inc.
 1997 Sandro Sigala
 2011 Shaun McCance
 2018 Tomasz Miąsko
 2010 Zach Goldberg
License: GPL-2+

Files:
 girepository/*.c
 girepository/*.h
 giscanner/giscannermodule.c
 giscanner/sourcescanner.c
 giscanner/sourcescanner.h
 giscanner/__init__.py
 giscanner/cachestore.py
 giscanner/gdumpparser.py
 giscanner/girparser.py
 giscanner/sourcescanner.py
 giscanner/transformer.py
 giscanner/utils.py
 giscanner/xmlwriter.py
 tools/compiler.c
 tools/generate.c
Copyright:
 2018 Christoph Reiter
 2014 Chun-wei Fan
 2008 Colin Walters
 2013 Dieter Verfaillie
 2011-2016 Dominique Leuenberger
 2016 Igor Gnatenko
 2007-2010 Johan Dahlin
 2007 Jürg Billeter
 2003-2005 Matthias Clasen
 2008 Philip Van Hoof
 2008-2013 Red Hat, Inc.
License: LGPL-2+

Files:
 girepository/cmph/*
Copyright:
 Davi de Castro Reis
 Fabiano Cupertino Botelho
License: LGPL-2 or MPL-1.1

Files:
 tests/scanner/identfilter.py
 tests/scanner/symbolfilter.py
Copyright:
 2014 Simon Feltman <sfeltman@gnome.org>
 2015 Garrett Regier <garrett.regier@riftio.com>
License: LGPL-2.1+

Files:
 giscanner/scannerlexer.l
 giscanner/scannerparser.y
Copyright:
 1997 Sandro Sigala
 2007-2008 Jürg Billeter
 2010 Andreas Rottmann
License: BSD-2-clause

Files:
 m4/introspection.m4
Copyright:
 2003-2005 Thomas Vander Stichele
 2009 Johan Dahlin
License: FSFAP and FSFULLR

Files: debian/*
Copyright:
 2023 Collabora Ltd.
 2008-2022 Debian contributors as listed in debian/changelog
 2021-2023 Simon McVittie
License: Expat and GPL-2+
Comment:
 No license was specified for the contents of debian/ prior to 2022. It
 is assumed to have been intended to be under the most restrictive of
 the upstream licenses, namely GPL-2+.
 The following contributors give permission to relicense their contributions
 to this package under either Expat, GPL-2+ or LGPL-2.1+ if desired:
 - Collabora Ltd.
 - Simon McVittie
 (Please add your name to this list if you wish to give this permission.)

# ----------------------------------------------------------------------

Files: glib/*
Comment:
 This directory is added by the Debian packaging to provide corresponding
 source code for gir/*.c, which are concatenations of doc-comments from the
 source files in glib/, in order to make it obvious that the preferred form
 for modification is included. The upstream plan is that responsibility for
 generating gir1.2-glib-2.0{,-dev} will move to the equivalent of Debian's
 src:glib2.0 during the GNOME 46 release cycle, at which point glib/ and
 gir/*.c can be dropped from src:gobject-introspection.
Copyright:
 2004-2005 Adam Weinberger
 2005-2006 Alexander Larsson
 2022 Alexander Shopov
 2021 Alexandros Theodotou
 2004 Anders Carlsson
 2001-2003 Andrew Lanoix
 2018 Arthur Demchenkov
 2001-2004 Behdad Esfahbod
 2006 Behdad Esfahbod
 2009 Benjamin Otte
 2009-2022 Canonical Ltd (Canonical Limited)
 2009-2022 Christian Hergert
 2010 Christian Kellner
 2010 Christian Persch
 2014-2015 Chun-wei Fan
 2008 Claus Tondering
 2008 Clemens N. Buss
 2008-2014 Codethink Ltd (Codethink Limited)
 2012-2013 Colin Walters
 2017-2018 Collabora Inc
 2008-2022 Collabora Ltd
 1999-2000 Craig Setera
 2000 Eazel, Inc
 2005-2023 Emmanuele Bassi
 2022 Emmanuel Fleury
 2016-2020 Endless Mobile, Inc
 2017-2022 Endless OS Foundation, LLC
 2007 Francois Gouget
 2020-2021 Frederic Martinsons
 1991-2022 Free Software Foundation, Inc
 2015 Garrett Regier
 2001-2022 GLib contributors
 2016 GNOME i18n Project for Vietnamese
 2019 GNOME
 2011 Google, Inc
 2001-2008 Hans Breuer
 2001 Hidetoshi Tajima
 2021 Iain Lane
 2018-2021 Igalia S.L
 2005-2008 Imendio AB
 2018 Iñigo Martínez
 2010 Intel Corp
 2001 James Henstridge
 2018-2019 James Westman
 2014-2018 Jan-Michael Brummer
 2005-2007 John McCutchan
 1995-1998 Josh MacDonald
 2007 Jürg Billeter
 2010 Karo Mkrtchyan
 2013-2015 Lars Uebernickel
 2006 Lukas Novotny
 2002 Manish Singh
 2005-2007 Marco Barisione
 2022 Marco Trevisan
 2001-2013 Matthias Clasen
 2010 Mikhail Zabaluev
 2004-2005 Miloslav Trmac
 2014 NICE s.r.l
 2003 Noah Levitt
 2008-2011 Nokia Corporation
 2008-2010 Novell, Inc
 2021 Ole André Vadla Ravnås
 2007 Openismus GmbH
 1998-2001 Owen Taylor
 2014-2019 Patrick Griffis
 2007 Patrick Hulin
 2012 Pavel Vasin
 2018 pdknsk
 1995-2011 Peter Mattis
 1995-2011 Peter Mattis, Spencer Kimball, Josh MacDonald, Sebastian Wilhelmi and others
 2022 Philip Withnall
 1998-2022 Red Hat, Inc
 1999-2003 Red Hat Software
 2001 Ron Steinke
 2020 Ruslan N. Marchenko
 2022 Ryan Hope
 2007-2015 Ryan Lortie
 2009-2010 Sam Thursfield
 2008 Samuel Cormier-Iijima
 1999-2000 Scott Wimer
 2007-2020 Sebastian Dröge
 2001-2003 Sebastian Wilhelmi
 1998-2001 Sebastian Wilhelmi; University of Karlsruhe
 2002-2006 Sharif FarsiWeb, Inc
 2012 Simon McVittie
 2011 Sjoerd Simons
 2002-2007 Soeren Sandmann
 1995-1998 Spencer Kimball
 2006 Stefan Westerfeld
 2011-2013 Stef Walter
 2007-2010 Sven Herzberg
 2012 Swecha telugu localisation Team
 2011-2022 systemd contributors
 2002-2022 the author(s) of GLib
 2006 The GNOME Foundation
 2007-2011 The GNOME Project
 2010 Thiago Santos
 1997-2007 Tim Janik
 2018 Tomasz Miąsko
 1999-2000 Tom Tromey
 1998-2004 Tor Lillqvist
 1995-1997 Ulrich Drepper
 2021 Unicode®, Inc
 2011 William Hua
 2018 Will Thompson
 2020 Xavier Claessens
 2021 Xavier Claessens
 2000-2004 Ximian Inc
 2014-2020 Руслан Ижбулатов
 Croatiann team
 Matthew Waters
License: LGPL-2+ and LGPL-2.1+ and FSFULLR and CC0-1.0

Files:
 glib/gio/xdgmime/xdgmime*
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright:
 2003-2004 Jonathan Blandford
 2004-2005 Matthias Clasen
 2003-2008 Red Hat, Inc
License: AFL-2.0 or LGPL-2.1+

Files:
 glib/glib/tests/NormalizationTest.txt
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright:
 2021 Unicode®, Inc.
License: Unicode-DFS-2016

Files:
 glib/gio/kqueue/*.?
 glib/*/tests/taptestrunner.py
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright:
 2011-2012 Dmitry Matveev
 2015 Remko Tronçon
License: Expat

Files:
 glib/glib/gen-unicode-tables.pl
 glib/glib/tests/gen-casefold-txt.py
 glib/glib/tests/gen-casemap-txt.py
 glib/po/po2tbl.sed.in
 glib/tools/glib-gettextize.in
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright:
 1989-2001 Free Software Foundation, Inc
 2001 Red Hat Software
 1998-1999 Tom Tromey
License: GPL-2+

Files:
 glib/gio/tests/memory-monitor-dbus.py.in
 glib/gio/tests/memory-monitor-portal.py.in
 glib/gio/tests/power-profile-monitor-dbus.py.in
 glib/gio/tests/power-profile-monitor-portal.py.in
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright: 2019-2021 Red Hat, Inc
License: LGPL-3+

Files:
 glib/.gitlab-ci/clang-format-diff.py
 glib/fuzzing/driver.c
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright: 2018, LLVM contributors
License: Apache-2.0 with LLVM exception

Files:
 glib/glib/gchecksum.c
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright:
 1995 A.M. Kuchling
 2006 Dave Benson
 2007 Emmanuele Bassi
License: LGPL-2.1+ and Kuchling-PD and Plumb-PD

Files:
 glib/glib/valgrind.h
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright:
 2000-2017, Julian Seward
License: bzip2-1.0.6

Files:
 glib/docs/reference/gio/menu-model.png
Comment:
 Unused in this particular package, see comment in glib/* entry above.
Copyright: unspecified
License: CC-BY-SA-3.0

Files: glib/m4macros/glib-gettext.m4
Copyright:
 1995-2002 Free Software Foundation, Inc.
 2001-2004 Red Hat, Inc.
License: GPL with Autoconf exception

# ----------------------------------------------------------------------

License: AFL-2.0
 The Academic Free License
 v. 2.0
 .
 This Academic Free License (the "License") applies to any original work
 of authorship (the "Original Work") whose owner (the "Licensor") has
 placed the following notice immediately following the copyright notice
 for the Original Work:
 .
      Licensed under the Academic Free License version 2.0
 .
 1) Grant of Copyright License. Licensor hereby grants You a world-wide,
 royalty-free, non-exclusive, perpetual, sublicenseable license to do
 the following:
 .
      a) to reproduce the Original Work in copies;
      b) to prepare derivative works ("Derivative Works") based upon the Original Work;
      c) to distribute copies of the Original Work and Derivative Works to the public;
      d) to perform the Original Work publicly; and
      e) to display the Original Work publicly.
 .
 2) Grant of Patent License. Licensor hereby grants You a world-wide,
 royalty-free, non-exclusive, perpetual, sublicenseable license, under
 patent claims owned or controlled by the Licensor that are embodied in
 the Original Work as furnished by the Licensor, to make, use, sell and
 offer for sale the Original Work and Derivative Works.
 .
 3) Grant of Source Code License. The term "Source Code" means the
 preferred form of the Original Work for making modifications to it and
 all available documentation describing how to modify the Original Work.
 Licensor hereby agrees to provide a machine-readable copy of the Source
 Code of the Original Work along with each copy of the Original Work
 that Licensor distributes.  Licensor reserves the right to satisfy this
 obligation by placing a machine-readable copy of the Source Code in an
 information repository reasonably calculated to permit inexpensive and
 convenient access by You for as long as Licensor continues to distribute
 the Original Work, and by publishing the address of that information
 repository in a notice immediately following the copyright notice that
 applies to the Original Work.
 .
 4) Exclusions From License Grant. Neither the names of Licensor, nor
 the names of any contributors to the Original Work, nor any of their
 trademarks or service marks, may be used to endorse or promote products
 derived from this Original Work without express prior written permission
 of the Licensor.  Nothing in this License shall be deemed to grant any
 rights to trademarks, copyrights, patents, trade secrets or any other
 intellectual property of Licensor except as expressly stated herein.
 No patent license is granted to make, use, sell or offer to sell
 embodiments of any patent claims other than the licensed claims defined
 in Section 2.  No right is granted to the trademarks of Licensor even if
 such marks are included in the Original Work.  Nothing in this License
 shall be interpreted to prohibit Licensor from licensing under different
 terms from this License any Original Work that Licensor otherwise would
 have a right to license.
 .
 5) This section intentionally omitted.
 .
 6) Attribution Rights. You must retain, in the Source Code of any
 Derivative Works that You create, all copyright, patent or trademark
 notices from the Source Code of the Original Work, as well as any
 notices of licensing and any descriptive text identified therein as an
 "Attribution Notice."  You must cause the Source Code for any Derivative
 Works that You create to carry a prominent Attribution Notice reasonably
 calculated to inform recipients that You have modified the Original Work.
 .
 7) Warranty of Provenance and Disclaimer of Warranty. Licensor warrants
 that the copyright in and to the Original Work and the patent rights
 granted herein by Licensor are owned by the Licensor or are sublicensed
 to You under the terms of this License with the permission of the
 contributor(s) of those copyrights and patent rights.  Except as
 expressly stated in the immediately proceeding sentence, the Original
 Work is provided under this License on an "AS IS" BASIS and WITHOUT
 WARRANTY, either express or implied, including, without limitation,
 the warranties of NON-INFRINGEMENT, MERCHANTABILITY or FITNESS FOR A
 PARTICULAR PURPOSE.  THE ENTIRE RISK AS TO THE QUALITY OF THE ORIGINAL
 WORK IS WITH YOU.  This DISCLAIMER OF WARRANTY constitutes an essential
 part of this License.  No license to Original Work is granted hereunder
 except under this disclaimer.
 .
 8) Limitation of Liability. Under no circumstances and under no legal
 theory, whether in tort (including negligence), contract, or otherwise,
 shall the Licensor be liable to any person for any direct, indirect,
 special, incidental, or consequential damages of any character arising
 as a result of this License or the use of the Original Work including,
 without limitation, damages for loss of goodwill, work stoppage, computer
 failure or malfunction, or any and all other commercial damages or losses.
 This limitation of liability shall not apply to liability for death
 or personal injury resulting from Licensor's negligence to the extent
 applicable law prohibits such limitation.  Some jurisdictions do not
 allow the exclusion or limitation of incidental or consequential damages,
 so this exclusion and limitation may not apply to You.
 .
 9) Acceptance and Termination. If You distribute  copies of the Original
 Work or a Derivative Work, You must make a reasonable effort under the
 circumstances to obtain the express assent of recipients to the terms of
 this License.  Nothing else but this License (or another written agreement
 between Licensor and You) grants You permission to create Derivative Works
 based upon the Original Work or to exercise any of the rights granted
 in Section 1 herein, and any attempt to do so except under the terms of
 this License (or another written agreement between Licensor and You)
 is expressly prohibited by U.S. copyright law, the equivalent laws of
 other countries, and by international treaty.  Therefore, by exercising
 any of the rights granted to You in Section 1 herein, You indicate Your
 acceptance of this License and all of its terms and conditions.
 .
 10) Termination for Patent Action. This License shall terminate
 automatically and You may no longer exercise any of the rights granted
 to You by this License as of the date You commence an action, including a
 cross-claim or counterclaim, for patent infringement (i) against Licensor
 with respect to a patent applicable to software or (ii) against any entity
 with respect to a patent applicable to the Original Work (but excluding
 combinations of the Original Work with other software or hardware).
 .
 11) Jurisdiction, Venue and Governing Law. Any action or suit relating to
 this License may be brought only in the courts of a jurisdiction wherein
 the Licensor resides or in which Licensor conducts its primary business,
 and under the laws of that jurisdiction excluding its conflict-of-law
 provisions.  The application of the United Nations Convention on Contracts
 for the International Sale of Goods is expressly excluded.  Any use of the
 Original Work outside the scope of this License or after its termination
 shall be subject to the requirements and penalties of the U.S. Copyright
 Act, 17 U.S.C. ¤ 101 et seq., the equivalent laws of other countries,
 and international treaty.  This section shall survive the termination
 of this License.
 .
 12) Attorneys Fees. In any action to enforce the terms of this License or
 seeking damages relating thereto, the prevailing party shall be entitled
 to recover its costs and expenses, including, without limitation,
 reasonable attorneys' fees and costs incurred in connection with such
 action, including any appeal of such action.  This section shall survive
 the termination of this License.
 .
 13) Miscellaneous. This License represents the complete agreement
 concerning the subject matter hereof.  If any provision of this License
 is held to be unenforceable, such provision shall be reformed only to
 the extent necessary to make it enforceable.
 .
 14) Definition of "You" in This License. "You" throughout this License,
 whether in upper or lower case, means an individual or a legal entity
 exercising rights under, and complying with all of the terms of, this
 License.  For legal entities, "You" includes any entity that controls,
 is controlled by, or is under common control with you.  For purposes
 of this definition, "control" means (i) the power, direct or indirect,
 to cause the direction or management of such entity, whether by contract
 or otherwise, or (ii) ownership of fifty percent (50%) or more of the
 outstanding shares, or (iii) beneficial ownership of such entity.
 .
 15) Right to Use. You may use the Original Work in all ways not otherwise
 restricted or conditioned by this License or by law, and Licensor promises
 not to interfere with or be responsible for such uses by You.
 .
 This license is Copyright (C) 2003 Lawrence E. Rosen.  All rights
 reserved.  Permission is hereby granted to copy and distribute this
 license without modification.  This license may not be modified without
 the express written permission of its copyright owner.

License: Apache-2.0 with LLVM exception
 See /usr/share/common-licenses/Apache-2.0 on a Debian system for the text
 of the Apache-2.0 license.
 .
 As an exception, if, as a result of your compiling your source code, portions
 of this Software are embedded into an Object form of such source code, you
 may redistribute such embedded portions in such Object form without complying
 with the conditions of Sections 4(a), 4(b) and 4(d) of the License.
 .
 In addition, if you combine or link compiled forms of this Software with
 software that is licensed under the GPLv2 ("Combined Software") and if a
 court of competent jurisdiction determines that the patent provision (Section
 3), the indemnity provision (Section 9) or other Section of the License
 conflicts with the conditions of the GPLv2, you may retroactively and
 prospectively choose to deem waived or otherwise exclude such Section(s) of
 the License, but only in their entirety and only with respect to the Combined
 Software.

License: BSD-2-clause
  Redistribution and use in source and binary forms, with or without
  modification, are permitted provided that the following conditions
  are met:
  1. Redistributions of source code must retain the above copyright
     notice, this list of conditions and the following disclaimer.
  2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.
  .
  THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
  IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
  OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
  NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
  DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
  THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
  THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

License: bzip2-1.0.6
 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions
 are met:
 .
 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 .
 2. The origin of this software must not be misrepresented; you must
    not claim that you wrote the original software.  If you use this
    software in a product, an acknowledgment in the product
    documentation would be appreciated but is not required.
 .
 3. Altered source versions must be plainly marked as such, and must
    not be misrepresented as being the original software.
 .
 4. The name of the author may not be used to endorse or promote
    products derived from this software without specific prior written
    permission.
 .
 THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS
 OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
 DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
 GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
 WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
 NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

License: CC-BY-SA-3.0
 SPDX license expression "CC-BY-SA-3.0": https://spdx.org/licenses/CC-BY-SA-3.0.html
 .
 Creative Commons Legal Code
 .
 Attribution-ShareAlike 3.0 Unported
 .
     CREATIVE COMMONS CORPORATION IS NOT A LAW FIRM AND DOES NOT PROVIDE
     LEGAL SERVICES. DISTRIBUTION OF THIS LICENSE DOES NOT CREATE AN
     ATTORNEY-CLIENT RELATIONSHIP. CREATIVE COMMONS PROVIDES THIS
     INFORMATION ON AN "AS-IS" BASIS. CREATIVE COMMONS MAKES NO WARRANTIES
     REGARDING THE INFORMATION PROVIDED, AND DISCLAIMS LIABILITY FOR
     DAMAGES RESULTING FROM ITS USE.
 .
 License
 .
 THE WORK (AS DEFINED BELOW) IS PROVIDED UNDER THE TERMS OF THIS CREATIVE
 COMMONS PUBLIC LICENSE ("CCPL" OR "LICENSE"). THE WORK IS PROTECTED BY
 COPYRIGHT AND/OR OTHER APPLICABLE LAW. ANY USE OF THE WORK OTHER THAN AS
 AUTHORIZED UNDER THIS LICENSE OR COPYRIGHT LAW IS PROHIBITED.
 .
 BY EXERCISING ANY RIGHTS TO THE WORK PROVIDED HERE, YOU ACCEPT AND AGREE
 TO BE BOUND BY THE TERMS OF THIS LICENSE. TO THE EXTENT THIS LICENSE MAY
 BE CONSIDERED TO BE A CONTRACT, THE LICENSOR GRANTS YOU THE RIGHTS
 CONTAINED HERE IN CONSIDERATION OF YOUR ACCEPTANCE OF SUCH TERMS AND
 CONDITIONS.
 .
 1. Definitions
 .
  a. "Adaptation" means a work based upon the Work, or upon the Work and
     other pre-existing works, such as a translation, adaptation,
     derivative work, arrangement of music or other alterations of a
     literary or artistic work, or phonogram or performance and includes
     cinematographic adaptations or any other form in which the Work may be
     recast, transformed, or adapted including in any form recognizably
     derived from the original, except that a work that constitutes a
     Collection will not be considered an Adaptation for the purpose of
     this License. For the avoidance of doubt, where the Work is a musical
     work, performance or phonogram, the synchronization of the Work in
     timed-relation with a moving image ("synching") will be considered an
     Adaptation for the purpose of this License.
  b. "Collection" means a collection of literary or artistic works, such as
     encyclopedias and anthologies, or performances, phonograms or
     broadcasts, or other works or subject matter other than works listed
     in Section 1(f) below, which, by reason of the selection and
     arrangement of their contents, constitute intellectual creations, in
     which the Work is included in its entirety in unmodified form along
     with one or more other contributions, each constituting separate and
     independent works in themselves, which together are assembled into a
     collective whole. A work that constitutes a Collection will not be
     considered an Adaptation (as defined below) for the purposes of this
     License.
  c. "Creative Commons Compatible License" means a license that is listed
     at https://creativecommons.org/compatiblelicenses that has been
     approved by Creative Commons as being essentially equivalent to this
     License, including, at a minimum, because that license: (i) contains
     terms that have the same purpose, meaning and effect as the License
     Elements of this License; and, (ii) explicitly permits the relicensing
     of adaptations of works made available under that license under this
     License or a Creative Commons jurisdiction license with the same
     License Elements as this License.
  d. "Distribute" means to make available to the public the original and
     copies of the Work or Adaptation, as appropriate, through sale or
     other transfer of ownership.
  e. "License Elements" means the following high-level license attributes
     as selected by Licensor and indicated in the title of this License:
     Attribution, ShareAlike.
  f. "Licensor" means the individual, individuals, entity or entities that
     offer(s) the Work under the terms of this License.
  g. "Original Author" means, in the case of a literary or artistic work,
     the individual, individuals, entity or entities who created the Work
     or if no individual or entity can be identified, the publisher; and in
     addition (i) in the case of a performance the actors, singers,
     musicians, dancers, and other persons who act, sing, deliver, declaim,
     play in, interpret or otherwise perform literary or artistic works or
     expressions of folklore; (ii) in the case of a phonogram the producer
     being the person or legal entity who first fixes the sounds of a
     performance or other sounds; and, (iii) in the case of broadcasts, the
     organization that transmits the broadcast.
  h. "Work" means the literary and/or artistic work offered under the terms
     of this License including without limitation any production in the
     literary, scientific and artistic domain, whatever may be the mode or
     form of its expression including digital form, such as a book,
     pamphlet and other writing; a lecture, address, sermon or other work
     of the same nature; a dramatic or dramatico-musical work; a
     choreographic work or entertainment in dumb show; a musical
     composition with or without words; a cinematographic work to which are
     assimilated works expressed by a process analogous to cinematography;
     a work of drawing, painting, architecture, sculpture, engraving or
     lithography; a photographic work to which are assimilated works
     expressed by a process analogous to photography; a work of applied
     art; an illustration, map, plan, sketch or three-dimensional work
     relative to geography, topography, architecture or science; a
     performance; a broadcast; a phonogram; a compilation of data to the
     extent it is protected as a copyrightable work; or a work performed by
     a variety or circus performer to the extent it is not otherwise
     considered a literary or artistic work.
  i. "You" means an individual or entity exercising rights under this
     License who has not previously violated the terms of this License with
     respect to the Work, or who has received express permission from the
     Licensor to exercise rights under this License despite a previous
     violation.
  j. "Publicly Perform" means to perform public recitations of the Work and
     to communicate to the public those public recitations, by any means or
     process, including by wire or wireless means or public digital
     performances; to make available to the public Works in such a way that
     members of the public may access these Works from a place and at a
     place individually chosen by them; to perform the Work to the public
     by any means or process and the communication to the public of the
     performances of the Work, including by public digital performance; to
     broadcast and rebroadcast the Work by any means including signs,
     sounds or images.
  k. "Reproduce" means to make copies of the Work by any means including
     without limitation by sound or visual recordings and the right of
     fixation and reproducing fixations of the Work, including storage of a
     protected performance or phonogram in digital form or other electronic
     medium.
 .
 2. Fair Dealing Rights. Nothing in this License is intended to reduce,
 limit, or restrict any uses free from copyright or rights arising from
 limitations or exceptions that are provided for in connection with the
 copyright protection under copyright law or other applicable laws.
 .
 3. License Grant. Subject to the terms and conditions of this License,
 Licensor hereby grants You a worldwide, royalty-free, non-exclusive,
 perpetual (for the duration of the applicable copyright) license to
 exercise the rights in the Work as stated below:
 .
  a. to Reproduce the Work, to incorporate the Work into one or more
     Collections, and to Reproduce the Work as incorporated in the
     Collections;
  b. to create and Reproduce Adaptations provided that any such Adaptation,
     including any translation in any medium, takes reasonable steps to
     clearly label, demarcate or otherwise identify that changes were made
     to the original Work. For example, a translation could be marked "The
     original work was translated from English to Spanish," or a
     modification could indicate "The original work has been modified.";
  c. to Distribute and Publicly Perform the Work including as incorporated
     in Collections; and,
  d. to Distribute and Publicly Perform Adaptations.
  e. For the avoidance of doubt:
 .
      i. Non-waivable Compulsory License Schemes. In those jurisdictions in
         which the right to collect royalties through any statutory or
         compulsory licensing scheme cannot be waived, the Licensor
         reserves the exclusive right to collect such royalties for any
         exercise by You of the rights granted under this License;
     ii. Waivable Compulsory License Schemes. In those jurisdictions in
         which the right to collect royalties through any statutory or
         compulsory licensing scheme can be waived, the Licensor waives the
         exclusive right to collect such royalties for any exercise by You
         of the rights granted under this License; and,
    iii. Voluntary License Schemes. The Licensor waives the right to
         collect royalties, whether individually or, in the event that the
         Licensor is a member of a collecting society that administers
         voluntary licensing schemes, via that society, from any exercise
         by You of the rights granted under this License.
 .
 The above rights may be exercised in all media and formats whether now
 known or hereafter devised. The above rights include the right to make
 such modifications as are technically necessary to exercise the rights in
 other media and formats. Subject to Section 8(f), all rights not expressly
 granted by Licensor are hereby reserved.
 .
 4. Restrictions. The license granted in Section 3 above is expressly made
 subject to and limited by the following restrictions:
 .
  a. You may Distribute or Publicly Perform the Work only under the terms
     of this License. You must include a copy of, or the Uniform Resource
     Identifier (URI) for, this License with every copy of the Work You
     Distribute or Publicly Perform. You may not offer or impose any terms
     on the Work that restrict the terms of this License or the ability of
     the recipient of the Work to exercise the rights granted to that
     recipient under the terms of the License. You may not sublicense the
     Work. You must keep intact all notices that refer to this License and
     to the disclaimer of warranties with every copy of the Work You
     Distribute or Publicly Perform. When You Distribute or Publicly
     Perform the Work, You may not impose any effective technological
     measures on the Work that restrict the ability of a recipient of the
     Work from You to exercise the rights granted to that recipient under
     the terms of the License. This Section 4(a) applies to the Work as
     incorporated in a Collection, but this does not require the Collection
     apart from the Work itself to be made subject to the terms of this
     License. If You create a Collection, upon notice from any Licensor You
     must, to the extent practicable, remove from the Collection any credit
     as required by Section 4(c), as requested. If You create an
     Adaptation, upon notice from any Licensor You must, to the extent
     practicable, remove from the Adaptation any credit as required by
     Section 4(c), as requested.
  b. You may Distribute or Publicly Perform an Adaptation only under the
     terms of: (i) this License; (ii) a later version of this License with
     the same License Elements as this License; (iii) a Creative Commons
     jurisdiction license (either this or a later license version) that
     contains the same License Elements as this License (e.g.,
     Attribution-ShareAlike 3.0 US)); (iv) a Creative Commons Compatible
     License. If you license the Adaptation under one of the licenses
     mentioned in (iv), you must comply with the terms of that license. If
     you license the Adaptation under the terms of any of the licenses
     mentioned in (i), (ii) or (iii) (the "Applicable License"), you must
     comply with the terms of the Applicable License generally and the
     following provisions: (I) You must include a copy of, or the URI for,
     the Applicable License with every copy of each Adaptation You
     Distribute or Publicly Perform; (II) You may not offer or impose any
     terms on the Adaptation that restrict the terms of the Applicable
     License or the ability of the recipient of the Adaptation to exercise
     the rights granted to that recipient under the terms of the Applicable
     License; (III) You must keep intact all notices that refer to the
     Applicable License and to the disclaimer of warranties with every copy
     of the Work as included in the Adaptation You Distribute or Publicly
     Perform; (IV) when You Distribute or Publicly Perform the Adaptation,
     You may not impose any effective technological measures on the
     Adaptation that restrict the ability of a recipient of the Adaptation
     from You to exercise the rights granted to that recipient under the
     terms of the Applicable License. This Section 4(b) applies to the
     Adaptation as incorporated in a Collection, but this does not require
     the Collection apart from the Adaptation itself to be made subject to
     the terms of the Applicable License.
  c. If You Distribute, or Publicly Perform the Work or any Adaptations or
     Collections, You must, unless a request has been made pursuant to
     Section 4(a), keep intact all copyright notices for the Work and
     provide, reasonable to the medium or means You are utilizing: (i) the
     name of the Original Author (or pseudonym, if applicable) if supplied,
     and/or if the Original Author and/or Licensor designate another party
     or parties (e.g., a sponsor institute, publishing entity, journal) for
     attribution ("Attribution Parties") in Licensor's copyright notice,
     terms of service or by other reasonable means, the name of such party
     or parties; (ii) the title of the Work if supplied; (iii) to the
     extent reasonably practicable, the URI, if any, that Licensor
     specifies to be associated with the Work, unless such URI does not
     refer to the copyright notice or licensing information for the Work;
     and (iv) , consistent with Ssection 3(b), in the case of an
     Adaptation, a credit identifying the use of the Work in the Adaptation
     (e.g., "French translation of the Work by Original Author," or
     "Screenplay based on original Work by Original Author"). The credit
     required by this Section 4(c) may be implemented in any reasonable
     manner; provided, however, that in the case of a Adaptation or
     Collection, at a minimum such credit will appear, if a credit for all
     contributing authors of the Adaptation or Collection appears, then as
     part of these credits and in a manner at least as prominent as the
     credits for the other contributing authors. For the avoidance of
     doubt, You may only use the credit required by this Section for the
     purpose of attribution in the manner set out above and, by exercising
     Your rights under this License, You may not implicitly or explicitly
     assert or imply any connection with, sponsorship or endorsement by the
     Original Author, Licensor and/or Attribution Parties, as appropriate,
     of You or Your use of the Work, without the separate, express prior
     written permission of the Original Author, Licensor and/or Attribution
     Parties.
  d. Except as otherwise agreed in writing by the Licensor or as may be
     otherwise permitted by applicable law, if You Reproduce, Distribute or
     Publicly Perform the Work either by itself or as part of any
     Adaptations or Collections, You must not distort, mutilate, modify or
     take other derogatory action in relation to the Work which would be
     prejudicial to the Original Author's honor or reputation. Licensor
     agrees that in those jurisdictions (e.g. Japan), in which any exercise
     of the right granted in Section 3(b) of this License (the right to
     make Adaptations) would be deemed to be a distortion, mutilation,
     modification or other derogatory action prejudicial to the Original
     Author's honor and reputation, the Licensor will waive or not assert,
     as appropriate, this Section, to the fullest extent permitted by the
     applicable national law, to enable You to reasonably exercise Your
     right under Section 3(b) of this License (right to make Adaptations)
     but not otherwise.
 .
 5. Representations, Warranties and Disclaimer
 .
 UNLESS OTHERWISE MUTUALLY AGREED TO BY THE PARTIES IN WRITING, LICENSOR
 OFFERS THE WORK AS-IS AND MAKES NO REPRESENTATIONS OR WARRANTIES OF ANY
 KIND CONCERNING THE WORK, EXPRESS, IMPLIED, STATUTORY OR OTHERWISE,
 INCLUDING, WITHOUT LIMITATION, WARRANTIES OF TITLE, MERCHANTIBILITY,
 FITNESS FOR A PARTICULAR PURPOSE, NONINFRINGEMENT, OR THE ABSENCE OF
 LATENT OR OTHER DEFECTS, ACCURACY, OR THE PRESENCE OF ABSENCE OF ERRORS,
 WHETHER OR NOT DISCOVERABLE. SOME JURISDICTIONS DO NOT ALLOW THE EXCLUSION
 OF IMPLIED WARRANTIES, SO SUCH EXCLUSION MAY NOT APPLY TO YOU.
 .
 6. Limitation on Liability. EXCEPT TO THE EXTENT REQUIRED BY APPLICABLE
 LAW, IN NO EVENT WILL LICENSOR BE LIABLE TO YOU ON ANY LEGAL THEORY FOR
 ANY SPECIAL, INCIDENTAL, CONSEQUENTIAL, PUNITIVE OR EXEMPLARY DAMAGES
 ARISING OUT OF THIS LICENSE OR THE USE OF THE WORK, EVEN IF LICENSOR HAS
 BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.
 .
 7. Termination
 .
  a. This License and the rights granted hereunder will terminate
     automatically upon any breach by You of the terms of this License.
     Individuals or entities who have received Adaptations or Collections
     from You under this License, however, will not have their licenses
     terminated provided such individuals or entities remain in full
     compliance with those licenses. Sections 1, 2, 5, 6, 7, and 8 will
     survive any termination of this License.
  b. Subject to the above terms and conditions, the license granted here is
     perpetual (for the duration of the applicable copyright in the Work).
     Notwithstanding the above, Licensor reserves the right to release the
     Work under different license terms or to stop distributing the Work at
     any time; provided, however that any such election will not serve to
     withdraw this License (or any other license that has been, or is
     required to be, granted under the terms of this License), and this
     License will continue in full force and effect unless terminated as
     stated above.
 .
 8. Miscellaneous
 .
  a. Each time You Distribute or Publicly Perform the Work or a Collection,
     the Licensor offers to the recipient a license to the Work on the same
     terms and conditions as the license granted to You under this License.
  b. Each time You Distribute or Publicly Perform an Adaptation, Licensor
     offers to the recipient a license to the original Work on the same
     terms and conditions as the license granted to You under this License.
  c. If any provision of this License is invalid or unenforceable under
     applicable law, it shall not affect the validity or enforceability of
     the remainder of the terms of this License, and without further action
     by the parties to this agreement, such provision shall be reformed to
     the minimum extent necessary to make such provision valid and
     enforceable.
  d. No term or provision of this License shall be deemed waived and no
     breach consented to unless such waiver or consent shall be in writing
     and signed by the party to be charged with such waiver or consent.
  e. This License constitutes the entire agreement between the parties with
     respect to the Work licensed here. There are no understandings,
     agreements or representations with respect to the Work not specified
     here. Licensor shall not be bound by any additional provisions that
     may appear in any communication from You. This License may not be
     modified without the mutual written agreement of the Licensor and You.
  f. The rights granted under, and the subject matter referenced, in this
     License were drafted utilizing the terminology of the Berne Convention
     for the Protection of Literary and Artistic Works (as amended on
     September 28, 1979), the Rome Convention of 1961, the WIPO Copyright
     Treaty of 1996, the WIPO Performances and Phonograms Treaty of 1996
     and the Universal Copyright Convention (as revised on July 24, 1971).
     These rights and subject matter take effect in the relevant
     jurisdiction in which the License terms are sought to be enforced
     according to the corresponding provisions of the implementation of
     those treaty provisions in the applicable national law. If the
     standard suite of rights granted under applicable copyright law
     includes additional rights not granted under this License, such
     additional rights are deemed to be included in the License; this
     License is not intended to restrict the license of any rights under
     applicable law.
 .
 .
 Creative Commons Notice
 .
     Creative Commons is not a party to this License, and makes no warranty
     whatsoever in connection with the Work. Creative Commons will not be
     liable to You or any party on any legal theory for any damages
     whatsoever, including without limitation any general, special,
     incidental or consequential damages arising in connection to this
     license. Notwithstanding the foregoing two (2) sentences, if Creative
     Commons has expressly identified itself as the Licensor hereunder, it
     shall have all rights and obligations of Licensor.
 .
     Except for the limited purpose of indicating to the public that the
     Work is licensed under the CCPL, Creative Commons does not authorize
     the use by either party of the trademark "Creative Commons" or any
     related trademark or logo of Creative Commons without the prior
     written consent of Creative Commons. Any permitted use will be in
     compliance with Creative Commons' then-current trademark usage
     guidelines, as may be published on its website or otherwise made
     available upon request from time to time. For the avoidance of doubt,
     this trademark restriction does not form part of the License.
 .
     Creative Commons may be contacted at https://creativecommons.org/.

License: CC0-1.0
 SPDX license expression "CC0-1.0": https://spdx.org/licenses/CC0-1.0.html
 On Debian systems, the complete text of the CC0 Public Domain Dedication
 can be found in "/usr/share/common-licenses/CC0-1.0".

License: Expat
 SPDX license expression "MIT": https://spdx.org/licenses/MIT.html
 .
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in
 all copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 THE SOFTWARE.

License: FSFAP
 This file is free software; the author(s) gives unlimited
 permission to copy and/or distribute it, with or without
 modifications, as long as this notice is preserved.

License: FSFULLR
 This file is free software; the Free Software Foundation
 gives unlimited permission to copy and/or distribute it,
 with or without modifications, as long as this notice is preserved.

License: GPL-2+
 This program is free software; you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation; either version 2, or (at your option)
 any later version.
 .
 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.
 .
 On Debian systems, a copy of the GPL version 2 can be found in
 /usr/share/common-licenses/GPL-2.

License: GPL with Autoconf exception
 This file is free software, distributed under the terms of the GNU
 General Public License.  As a special exception to the GNU General
 Public License, this file may be distributed as part of a program
 that contains a configuration script generated by Autoconf, under
 the same distribution terms as the rest of that program.

License: Kuchling-PD
 Distribute and use freely; there are no restrictions on further
 dissemination and usage except those imposed by the laws of your
 country of residence.

License: LGPL-2
 Code imported from cmph project, which describes it as "LGPL-2 and MPL 1.1"
 On Debian systems, a copy of the LGPL version 2 can be found in
 /usr/share/common-licenses/LGPL-2.

License: LGPL-2+
 The GNU CHARSET Library is free software; you can redistribute it and/or
 modify it under the terms of the GNU Library General Public License as
 published by the Free Software Foundation; either version 2 of the
 License, or (at your option) any later version.
 .
 The GNU CHARSET Library is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 Library General Public License for more details.
 .
 On Debian systems, a copy of the LGPL version 2 can be found in
 /usr/share/common-licenses/LGPL-2.

License: LGPL-2.1+
 This library is free software; you can redistribute it and/or
 modify it under the terms of the GNU Lesser General Public
 License as published by the Free Software Foundation; either
 version 2.1 of the License, or (at your option) any later version.
 .
 This library is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 Lesser General Public License for more details.
 .
 On Debian systems, a copy of the LGPL version 2.1 can be found in
 /usr/share/common-licenses/LGPL-2.1.

License: LGPL-3+
 This program is free software; you can redistribute it and/or modify it under
 the terms of the GNU Lesser General Public License as published by the Free
 Software Foundation; either version 3 of the License, or (at your option) any
 later version.  See http://www.gnu.org/copyleft/lgpl.html for the full text
 of the license.
 .
 On Debian systems, a copy of the LGPL version 3 can be found in
 /usr/share/common-licenses/LGPL-3.

License: MPL-1.1
 Code imported from cmph project, which describes it as "LGPL-2 and MPL 1.1"
 On Debian systems, a copy of the MPL version 1.1 can be found in
 /usr/share/common-licenses/MPL-1.1.

License: Plumb-PD
 The algorithm is due to Ron Rivest.  This code was
 written by Colin Plumb in 1993, no copyright is claimed.
 This code is in the public domain; do with it what you wish.

License: Unicode-DFS-2016
 See Terms of Use <https://www.unicode.org/copyright.html>
 for definitions of Unicode Inc.’s Data Files and Software.
 .
 NOTICE TO USER: Carefully read the following legal agreement.
 BY DOWNLOADING, INSTALLING, COPYING OR OTHERWISE USING UNICODE INC.'S
 DATA FILES ("DATA FILES"), AND/OR SOFTWARE ("SOFTWARE"),
 YOU UNEQUIVOCALLY ACCEPT, AND AGREE TO BE BOUND BY, ALL OF THE
 TERMS AND CONDITIONS OF THIS AGREEMENT.
 IF YOU DO NOT AGREE, DO NOT DOWNLOAD, INSTALL, COPY, DISTRIBUTE OR USE
 THE DATA FILES OR SOFTWARE.
 .
 COPYRIGHT AND PERMISSION NOTICE
 .
 Copyright © 1991-2022 Unicode, Inc. All rights reserved.
 Distributed under the Terms of Use in https://www.unicode.org/copyright.html.
 .
 Permission is hereby granted, free of charge, to any person obtaining
 a copy of the Unicode data files and any associated documentation
 (the "Data Files") or Unicode software and any associated documentation
 (the "Software") to deal in the Data Files or Software
 without restriction, including without limitation the rights to use,
 copy, modify, merge, publish, distribute, and/or sell copies of
 the Data Files or Software, and to permit persons to whom the Data Files
 or Software are furnished to do so, provided that either
 (a) this copyright and permission notice appear with all copies
 of the Data Files or Software, or
 (b) this copyright and permission notice appear in associated
 Documentation.
 .
 THE DATA FILES AND SOFTWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF
 ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
 WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
 NONINFRINGEMENT OF THIRD PARTY RIGHTS.
```

---

## libhwloc-plugins:2.10.0-1build1

**License Type:** See license text below

**Source:** Debian/APT

```
This package was debianized by Samuel Thibault <sthibault@debian.org> on
Mon, 06 Jul 2009 10:55:29 +0200.

It was downloaded from http://www.open-mpi.org/projects/hwloc/

Upstream Authors:

    Cédric Augonnet <Cedric.Augonnet@labri.fr>
    Jérôme Clet-Ortega <Jerome.Clet-Ortega@labri.fr>
    Ludovic Courtès <Ludovic.Courtes@inria.fr>
    Brice Goglin <Brice.Goglin@inria.fr>
    Nathalie Furmento <Nathalie.Furmento@labri.fr>
    Samuel Thibault <Samuel.Thibault@labri.fr>
    Jeff Squyres <jsquyres@cisco.com>

Copyright:

    Copyright © 2009 CNRS
    Copyright © 2009-2014 inria.  All rights reserved.
    Copyright © 2009-2014 Université Bordeaux 1
    Copyright © 2009-2014 Cisco Systems, Inc.  All rights reserved.

License:

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions
    are met:
    1. Redistributions of source code must retain the above copyright
       notice, this list of conditions and the following disclaimer.
    2. Redistributions in binary form must reproduce the above copyright
       notice, this list of conditions and the following disclaimer in the
       documentation and/or other materials provided with the distribution.
    3. The name of the author may not be used to endorse or promote products
       derived from this software without specific prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
    IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
    OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
    IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
    INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
    NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
    DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
    THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
    (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
    THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

The Debian packaging is:

    Copyright (C) 2009-2014 Samuel Thibault <sthibault@debian.org>

and is licensed under the GPL version 3, 
see `/usr/share/common-licenses/GPL-3'.
```

---

## libxnvctrl0:610.57.04-1ubuntu1

**License Type:** GPL-2 / Expat-NVIDIA / other-MetroLink and other-XFree / Expat-Precision / Expat-RedHat / other-MetroLink / other-XFree / Expat / other-Metrolink

**Source:** Debian/APT

```
Format: http://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: nvidia-settings
Upstream-Contact: NVIDIA Corporation
Source: ftp://download.nvidia.com/XFree86/nvidia-settings/

Files: *
Copyright: (C) 2004-2014 NVIDIA Corporation
License: GPL-2

Files: samples/*
Copyright: (C) 2004-2013 NVIDIA Corporation
License: Expat-NVIDIA

Files: src/libXNVCtrl/*
Copyright: (C) 2008-2010 NVIDIA Corporation
License: Expat-NVIDIA

Files: src/XF86Config-parser/*
Copyright: (c) 1997  Metro Link Incorporated
           (c) 1997-2003 by The XFree86 Project, Inc.
License: other-MetroLink and other-XFree

Files: src/XF86Config-parser/DRI.c
Copyright: 1999 Precision Insight, Inc., Cedar Park, Texas.  All Rights Reserved.
License: Expat-Precision
 Permission is hereby granted, free of charge, to any person obtaining a
 copy of this software and associated documentation files (the "Software"),
 to deal in the Software without restriction, including without limitation
 the rights to use, copy, modify, merge, publish, distribute, sublicense,
 and/or sell copies of the Software, and to permit persons to whom the
 Software is furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice (including the next
 paragraph) shall be included in all copies or substantial portions of the
 Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
 PRECISION INSIGHT AND/OR ITS SUPPLIERS BE LIABLE FOR ANY CLAIM, DAMAGES OR
 OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
 ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
 DEALINGS IN THE SOFTWARE.

Files: src/XF86Config-parser/Extensions.c
Copyright: 2004 Red Hat Inc., Raleigh, North Carolina.  All Rights Reserved.
License: Expat-RedHat
 Permission is hereby granted, free of charge, to any person obtaining
 a copy of this software and associated documentation files (the
 "Software"), to deal in the Software without restriction, including
 without limitation on the rights to use, copy, modify, merge,
 publish, distribute, sublicense, and/or sell copies of the Software,
 and to permit persons to whom the Software is furnished to do so,
 subject to the following conditions:
 .
 The above copyright notice and this permission notice (including the
 next paragraph) shall be included in all copies or substantial
 portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
 EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
 MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
 NON-INFRINGEMENT.  IN NO EVENT SHALL RED HAT AND/OR THEIR SUPPLIERS
 BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
 ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
 CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.

Files: src/XF86Config-parser/Generate.c
Copyright: (C) 2005 NVIDIA Corporation
           1999-2002 Red Hat, Inc.
License: GPL-2

Files: src/XF86Config-parser/Merge.c
Copyright: (c) 1997  Metro Link Incorporated
License: other-MetroLink

Files: src/XF86Config-parser/Util.c
Copyright: (C) 2005 NVIDIA Corporation
License: GPL-2

Files: src/XF86Config-parser/configProcs.h
Copyright: (c) 1997-2001 by The XFree86 Project, Inc.
License: other-XFree

Files: src/jansson/*
Copyright: 2009-2014 Petri Lehtinen <petri@digip.org>
           2011-2012 Basile Starynkevitch <basile@starynkevitch.net>
           2011-2012 Graeme Smecher <graeme.smecher@mail.mcgill.ca>
License: Expat
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in
 all copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 THE SOFTWARE.

Files: debian/*
Copyright: © 2004-2010 Randall Donald <rdonald@debian.org>
           © 2009-2010 Fathi Boudra <fabo@debian.org>
           © 2011-2014 Andreas Beckmann <anbe@debian.org>
           © 2008 Stefan Potyra <sistpoty@ubuntu.com>
           © 2008 Timo Aaltonen <tepsipakki@ubuntu.com>
           © 2008 Emmet Hikory <persia@ubuntu.com>
           © 2008 Mario Limonciello <mario_limonciello@dell.com>
           © 2008-2015 Alberto Milone <alberto.milone@canonical.com>
           © 2010 Kees Cook <kees@ubuntu.com>
           © 2011 Bryce Harrington <bryce@ubuntu.com>
           © 2011 Stéphane Graber <stgraber@ubuntu.com>
           © 2012 Adam Conrad <adconrad@ubuntu.com>
           © 2012 Donald Siuchninski <dsiuchninski@gmail.com>
           © 2013 Daniel T Chen <crimsun@ubuntu.com>
           © 2015 Graham Inggs <graham@nerve.org.za>
License: GPL-2

License: GPL-2
 This package is free software; you can redistribute it and/or modify it
 under the terms of the GNU General Public License version 2 as published
 by the Free Software Foundation.
 .
 This program is distributed in the hope that it will be useful, but
 WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
 Public License for more details.
 .
 You should have received a copy of the GNU General Public License along
 with this package; if not, write to the Free Software Foundation, Inc.,
 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
 .
 On Debian systems, the full text of the GNU General Public License
 version 2 can be found in the file /usr/share/common-licenses/GPL-2.

License: Expat-NVIDIA
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice (including the next
 paragraph) shall be included in all copies or substantial portions of the
 Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.

License: other-Metrolink
 Permission is hereby granted, free of charge, to any person obtaining a
 copy of this software and associated documentation files (the "Software"),
 to deal in the Software without restriction, including without limitation
 the rights to use, copy, modify, merge, publish, distribute, sublicense,
 and/or sell copies of the Software, and to permit persons to whom the
 Software is furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in
 all copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
 THE X CONSORTIUM BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
 WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF
 OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.
 .
 Except as contained in this notice, the name of the Metro Link shall not be
 used in advertising or otherwise to promote the sale, use or other dealings
 in this Software without prior written authorization from Metro Link.

License: other-XFree
 Permission is hereby granted, free of charge, to any person obtaining a
 copy of this software and associated documentation files (the "Software"),
 to deal in the Software without restriction, including without limitation
 the rights to use, copy, modify, merge, publish, distribute, sublicense,
 and/or sell copies of the Software, and to permit persons to whom the
 Software is furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in
 all copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
 THE COPYRIGHT HOLDER(S) OR AUTHOR(S) BE LIABLE FOR ANY CLAIM, DAMAGES OR
 OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
 ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
 OTHER DEALINGS IN THE SOFTWARE.
 .
 Except as contained in this notice, the name of the copyright holder(s)
 and author(s) shall not be used in advertising or otherwise to promote
 the sale, use or other dealings in this Software without prior written
 authorization from the copyright holder(s) and author(s).
```

---

## netcat-openbsd:1.226-1ubuntu2

**License Type:** BSD-3-Clause / BSD-2-Clause

**Source:** Debian/APT

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Source: http://www.openbsd.org/cgi-bin/cvsweb/src/usr.bin/nc/
Upstream-Name: netcat

Files: netcat.c
Copyright: 2001 Eric Jackson <ericj@monkey.org>
License: BSD-3-Clause

Files: nc.1
Copyright: 1996 David Sacerdote
License: BSD-3-Clause

Files: atomicio.*
Copyright: 2006 Damien Miller
           2005 Anil Madhavapeddy
           1995,1999 Theo de Raadt
License: BSD-2-Clause

Files: socks.c
Copyright: 1999 Niklas Hallqvist
           2004, 2005 Damien Miller
License: BSD-2-Clause

Files: Makefile
Copyright: The OpenBSD project
License: BSD-3-Clause

Files: debian/*
Copyright: 2008, 2009, 2010 Decklin Foster <decklin@red-bean.com>
           2008, 2009, 2010 Soren Hansen <soren@ubuntu.com>
           2012 Aron Xu <aron@debian.org>
           2016-2022 Guilhem Moulin <guilhem@debian.org>
License: BSD-3-Clause

Files: debian/checks/* debian/tests/*
Copyright: 2021-2022 Guilhem Moulin <guilhem@debian.org>
License: BSD-3-Clause

License: BSD-2-Clause
 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions
 are met:
 .
 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.
 .
 THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
 IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
 OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
 IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
 INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
 NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
 THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

License: BSD-3-Clause
 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions
 are met:
 .
 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.
 3. The name of the author may not be used to endorse or promote products
    derived from this software without specific prior written permission.
 .
 THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
 IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
 OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
 IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
 INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
 NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
 THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

---

## nlohmann-json3-dev:3.11.3-1

**License Type:** Expat / U-OF-I-BSD-LIKE / BSD-3-clause

**Source:** Debian/APT

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: nlohmann-json
Source: https://github.com/nlohmann/json

Files: *
Copyright: 2013-2022 Niels Lohmann
License: Expat

Files: include/nlohmann/detail/conversions/to_chars.hpp
Copyright: 2009 Florian Loitsch
License: Expat

Files: include/nlohmann/detail/output/serializer.hpp
Copyright: 2008-2009 Bjoern Hoehrmann <bjoern@hoehrmann.de>
License: Expat

Files: debian/*
Copyright: 2016 Muri Nicanor <muri@immerda.ch>
  2018 Hubert Chathi <uhoreg@debian.org>
  2019-2023 Gianfranco Costamagna <locutusofborg@debian.org>
License: Expat

Files: tests/thirdparty/Fuzzer/*
Copyright: University of Illinois at Urbana-Champaign
License: U-OF-I-BSD-LIKE

Files: tools/amalgamate/*
Copyright: 2012, Erik Edlund <erik.edlund@32767.se>
License: BSD-3-clause

Files: tools/cpplint/*
Copyright: 2009 Google Inc. All rights reserved.
License: BSD-3-clause

License: Expat
 Permission is hereby granted, free of charge, to any person obtaining a copy of
 this software and associated documentation files (the "Software"), to deal in
 the Software without restriction, including without limitation the rights to
 use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
 of the Software, and to permit persons to whom the Software is furnished to do
 so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in all
 copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.

License: U-OF-I-BSD-LIKE
  ==============================================================================
 LLVM Release License
 ==============================================================================
 University of Illinois/NCSA
 Open Source License
 .
 Copyright (c) 2003-2017 University of Illinois at Urbana-Champaign.
 All rights reserved.
 .
 Developed by:
 .
     LLVM Team
 .
     University of Illinois at Urbana-Champaign
 .
     http://llvm.org
 .
 Permission is hereby granted, free of charge, to any person obtaining a copy of
 this software and associated documentation files (the "Software"), to deal with
 the Software without restriction, including without limitation the rights to
 use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
 of the Software, and to permit persons to whom the Software is furnished to do
 so, subject to the following conditions:
 .
     * Redistributions of source code must retain the above copyright notice,
       this list of conditions and the following disclaimers.
 .
     * Redistributions in binary form must reproduce the above copyright notice,
       this list of conditions and the following disclaimers in the
       documentation and/or other materials provided with the distribution.
 .
     * Neither the names of the LLVM Team, University of Illinois at
       Urbana-Champaign, nor the names of its contributors may be used to
       endorse or promote products derived from this Software without specific
       prior written permission.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
 FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL THE
 CONTRIBUTORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS WITH THE
 SOFTWARE.

License: BSD-3-clause
 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions are met:
 .
 1.  Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.
 .
 2.  Redistributions in binary form must reproduce the above copyright notice,
     this list of conditions and the following disclaimer in the documentation
     and/or other materials provided with the distribution.
 .
 3.  The name of the copyright holders may be used to endorse or promote
     products derived from this software without specific prior written
     permission.
 .
 THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
 LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 POSSIBILITY OF SUCH DAMAGE.
```

---

## ocl-icd-libopencl1:2.3.2-1build1

**License Type:** BSD-2-Clause

**Source:** Debian/APT

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: ocl-icd
Source: https://github.com/OCL-dev/ocl-icd/tags

Files: *
Copyright: 2012-2020 Brice Videau <brice.videau@imag.fr>
           2012-2020 Vincent Danjean <Vincent.Danjean@ens-lyon.org>
License: BSD-2-Clause

Files: debian/*
Copyright: 2012-2021 Vincent Danjean <vdanjean@debian.org>
 © 2018-2023 Andreas Beckmann <anbe@debian.org>
License: BSD-2-Clause

License: BSD-2-Clause
 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions
 are met:
 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.
 .
 THIS SOFTWARE IS PROVIDED BY THE REGENTS AND CONTRIBUTORS ``AS IS'' AND
 ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 ARE DISCLAIMED.  IN NO EVENT SHALL THE REGENTS OR CONTRIBUTORS BE LIABLE
 FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
 OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
 OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 SUCH DAMAGE.
```

---

## python3-gi:3.48.2-1

**License Type:** LGPL-2.1+ / Expat

**Source:** Debian/APT

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: pygobject
Upstream-Contact:
 John (J5) Palmieri <johnp@redhat.com>
 Tomeu Vizoso <tomeu.vizoso@collabora.co.uk>
Source: https://download.gnome.org/sources/pygobject/

Files: *
Copyright:
 1998-2003 James Henstridge
 2004-2009 Johan Dahlin
 2005 Oracle
 2006 Johannes Hoelzl
 2008 Gian Mario Tagliaretti
 2009 Simon van der Linden
 2010 Collabora Ltd.
 2011 Laszlo Pandy
 2012 Canonical Ltd.
 2015 Dustin Spicuzza
 2018 Nikita Churaev
 2018 Christoph Reiter
License: LGPL-2.1+
 This package is free software; you can redistribute it and/or
 modify it under the terms of the GNU Lesser General Public
 License as published by the Free Software Foundation; either
 version 2.1 of the License, or (at your option) any later version.
 .
 This package is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 Lesser General Public License for more details.
 .
 You should have received a copy of the GNU Lesser General Public
 License along with this package; if not, write to the Free Software
 Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA
 .
 On Debian systems, the complete text of the GNU Lesser General
 Public License can be found in `/usr/share/common-licenses/LGPL-2'.

Files:
 gi/pygi-property.*
 gi/pygi-signal-closure.h
 gi/pygi-foreign*
Copyright:
 Copyright (c) 2010  Collabora Ltd. <http://www.collabora.co.uk/>
 Copyright (c) 2011  Laszlo Pandy <lpandy@src.gnome.org>
 Copyright (c) 2010  litl, LLC
License: Expat
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to
 deal in the Software without restriction, including without limitation the
 rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
 sell copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in
 all copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
 FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
 IN THE SOFTWARE.
```

---

## python3-gst-1.0:1.24.1-1

**License Type:** LGPL-2.1+ / LGPL-2+

**Source:** Debian/APT

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: GStreamer GObject Introspection overrides for python 1.0
Upstream-Contact: gstreamer-devel@lists.freedesktop.org
Source: https://gstreamer.freedesktop.org
Comment: This package was debianized by Sebastian Dröge <slomo@debian.org> on
 Tue, 30 Sep 2013 11:59:10 +0200.

Files: gi/overrides/GstPbutils.py
Copyright: 2012, Alessandro Decina <alessandro.d@gmail.com>
License: LGPL-2.1+
 /usr/share/common-licenses/LGPL-2.1

Files: gi/overrides/Gst.py
Copyright: 2012, Thibault Saunier <thibault.saunier@collabora.com>
License: LGPL-2.1+
 /usr/share/common-licenses/LGPL-2.1

Files: gi/overrides/gstmodule.c
Copyright: 2002, David I. Lehn
  2012, Thibault Saunier <thibault.saunier@collabora.com>
License: LGPL-2+
 /usr/share/common-licenses/LGPL-2
```

---

## charset-normalizer:3.5.0

**License Type:** MIT

**Source:** PyPI

```
License: MIT

(Full license text not bundled in wheel; see project home page.)
```

---

## cuda-bindings:13.4.0b1

**License Type:** LicenseRef-NVIDIA-SOFTWARE-LICENSE

**Source:** PyPI

```
License: LicenseRef-NVIDIA-SOFTWARE-LICENSE

(Full license text not bundled in wheel; see project home page.)
```

---

## cuda-pathfinder:1.6.0

**License Type:** Apache-2.0

**Source:** PyPI

```
License: Apache-2.0

(Full license text not bundled in wheel; see project home page.)
```

---

## cuda-toolkit:13.0.3.0

**License Type:** See license text below

**Source:** PyPI

```
License: 

(Full license text not bundled in wheel; see project home page.)
```

---

## flatbuffers:25.12.19

**License Type:** Apache 2.0

**Source:** PyPI

```
License: Apache 2.0
License :: OSI Approved :: Apache Software License
(Full license text not bundled in wheel; see project home page.)
```

---

## fsspec:2026.7.0

**License Type:** BSD-3-Clause

**Source:** PyPI

```
License: BSD-3-Clause

(Full license text not bundled in wheel; see project home page.)
```

---

## ftfy:6.3.1

**License Type:** Apache-2.0

**Source:** PyPI

```
License: Apache-2.0

(Full license text not bundled in wheel; see project home page.)
```

---

## hf-xet:1.6.0

**License Type:** Apache-2.0

**Source:** PyPI

```
License: Apache-2.0
License :: OSI Approved :: Apache Software License
(Full license text not bundled in wheel; see project home page.)
```

---

## huggingface_hub:0.36.2

**License Type:** Apache

**Source:** PyPI

```
License: Apache
License :: OSI Approved :: Apache Software License
(Full license text not bundled in wheel; see project home page.)
```

---

## Jinja2:3.1.6

**License Type:** License :: OSI Approved :: BSD License

**Source:** PyPI

```
License: 
License :: OSI Approved :: BSD License
(Full license text not bundled in wheel; see project home page.)
```

---

## kafka-python:3.0.10

**License Type:** Apache-2.0

**Source:** PyPI

```
License: Apache-2.0

(Full license text not bundled in wheel; see project home page.)
```

---

## MarkupSafe:3.0.2

**License Type:** BSD-3-Clause

**Source:** PyPI

```
License: BSD-3-Clause

(Full license text not bundled in wheel; see project home page.)
```

---

## ml_dtypes:0.6.0

**License Type:** Apache-2.0

**Source:** PyPI

```
License: Apache-2.0

(Full license text not bundled in wheel; see project home page.)
```

---

## mpmath:1.3.0

**License Type:** BSD-3-Clause

**Source:** PyPI

```
Copyright (c) 2005-2021 Fredrik Johansson and mpmath contributors

All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

  a. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.
  b. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.
  c. Neither the name of the copyright holder nor the names of its
     contributors may be used to endorse or promote products derived
     from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE REGENTS OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH
DAMAGE.
```

---

## networkx:3.6.1

**License Type:** BSD-3-Clause

**Source:** PyPI

```
License: BSD-3-Clause

(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-cublas:13.1.1.3

**License Type:** LicenseRef-NVIDIA-Proprietary

**Source:** PyPI

```
License: LicenseRef-NVIDIA-Proprietary
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-cuda-cupti:13.0.85

**License Type:** LicenseRef-NVIDIA-Proprietary

**Source:** PyPI

```
License: LicenseRef-NVIDIA-Proprietary
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-cuda-nvrtc:13.0.88

**License Type:** LicenseRef-NVIDIA-Proprietary

**Source:** PyPI

```
License: LicenseRef-NVIDIA-Proprietary
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-cuda-runtime:13.0.96

**License Type:** LicenseRef-NVIDIA-Proprietary

**Source:** PyPI

```
License: LicenseRef-NVIDIA-Proprietary
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-cudnn-cu13:9.24.0.43

**License Type:** LicenseRef-NVIDIA-Proprietary

**Source:** PyPI

```
License: LicenseRef-NVIDIA-Proprietary

(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-cufft:12.0.0.61

**License Type:** LicenseRef-NVIDIA-Proprietary

**Source:** PyPI

```
License: LicenseRef-NVIDIA-Proprietary
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-cufile:1.15.1.6

**License Type:** LicenseRef-NVIDIA-Proprietary

**Source:** PyPI

```
License: LicenseRef-NVIDIA-Proprietary
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-curand:10.4.0.35

**License Type:** LicenseRef-NVIDIA-Proprietary

**Source:** PyPI

```
License: LicenseRef-NVIDIA-Proprietary
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-cusolver:12.0.4.66

**License Type:** LicenseRef-NVIDIA-Proprietary

**Source:** PyPI

```
License: LicenseRef-NVIDIA-Proprietary
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-cusparse:12.6.3.3

**License Type:** LicenseRef-NVIDIA-Proprietary

**Source:** PyPI

```
License: LicenseRef-NVIDIA-Proprietary
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-cusparselt-cu13:0.8.1

**License Type:** NVIDIA Proprietary Software

**Source:** PyPI

```
License: NVIDIA Proprietary Software

(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-nccl-cu13:2.30.7

**License Type:** BSD-3-Clause

**Source:** PyPI

```
License: BSD-3-Clause
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-nvjitlink:13.4.46rc1

**License Type:** LicenseRef-NVIDIA-Proprietary

**Source:** PyPI

```
License: LicenseRef-NVIDIA-Proprietary
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-nvshmem-cu13:3.4.5

**License Type:** BSD-3-Clause

**Source:** PyPI

```
License: BSD-3-Clause
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## nvidia-nvtx:13.0.85

**License Type:** Apache 2.0

**Source:** PyPI

```
License: Apache 2.0
License :: Other/Proprietary License
(Full license text not bundled in wheel; see project home page.)
```

---

## onnx:1.22.0

**License Type:** Apache-2.0

**Source:** PyPI

```
License: Apache-2.0

(Full license text not bundled in wheel; see project home page.)
```

---

## onnxruntime-gpu:1.28.0

**License Type:** MIT License

**Source:** PyPI

```
License: MIT License
License :: OSI Approved :: MIT License
(Full license text not bundled in wheel; see project home page.)
```

---

## open_clip_torch:3.3.0

**License Type:** MIT

**Source:** PyPI

```
License: MIT
License :: OSI Approved :: MIT License
(Full license text not bundled in wheel; see project home page.)
```

---

## pillow:12.2.0

**License Type:** MIT-CMU

**Source:** PyPI

```
License: MIT-CMU

(Full license text not bundled in wheel; see project home page.)
```

---

## protobuf:7.35.1

**License Type:** BSD-3-Clause

**Source:** PyPI

```
Copyright 2008 Google Inc.  All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

    * Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above
copyright notice, this list of conditions and the following disclaimer
in the documentation and/or other materials provided with the
distribution.
    * Neither the name of Google Inc. nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

Code generated by the Protocol Buffer compiler is owned by the owner
of the input file used when generating it.  This code is not
standalone and requires a support library to be linked with it.  This
support library is itself covered by the above license.
```

---

## psutil:7.2.2

**License Type:** BSD-3-Clause

**Source:** PyPI

```
BSD 3-Clause License

Copyright (c) 2009, Jay Loden, Dave Daeschler, Giampaolo Rodola
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

 * Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

 * Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

 * Neither the name of the psutil authors nor the names of its contributors
   may be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

---

## PyYAML:6.0.3

**License Type:** MIT

**Source:** PyPI

```
License: MIT
License :: OSI Approved :: MIT License
(Full license text not bundled in wheel; see project home page.)
```

---

## regex:2026.7.19

**License Type:** Apache-2.0 AND CNRI-Python

**Source:** PyPI

```
License: Apache-2.0 AND CNRI-Python

(Full license text not bundled in wheel; see project home page.)
```

---

## requests:2.34.2

**License Type:** Apache-2.0

**Source:** PyPI

```
License: Apache-2.0
License :: OSI Approved :: Apache Software License
(Full license text not bundled in wheel; see project home page.)
```

---

## safetensors:0.8.0

**License Type:** License :: OSI Approved :: Apache Software License

**Source:** PyPI

```
License: 
License :: OSI Approved :: Apache Software License
(Full license text not bundled in wheel; see project home page.)
```

---

## sentencepiece:0.2.2

**License Type:** Apache-2.0

**Source:** PyPI

```
License: Apache-2.0

(Full license text not bundled in wheel; see project home page.)
```

---

## sympy:1.14.0

**License Type:** BSD

**Source:** PyPI

```
License: BSD
License :: OSI Approved :: BSD License
(Full license text not bundled in wheel; see project home page.)
```

---

## timm:1.0.28

**License Type:** Apache-2.0

**Source:** PyPI

```
License: Apache-2.0
License :: OSI Approved :: Apache Software License
(Full license text not bundled in wheel; see project home page.)
```

---

## tokenizers:0.22.2

**License Type:** License :: OSI Approved :: Apache Software License

**Source:** PyPI

```
License: 
License :: OSI Approved :: Apache Software License
(Full license text not bundled in wheel; see project home page.)
```

---

## torch:2.15.0.dev20260813+cu130

**License Type:** BSD-3-Clause

**Source:** PyPI

```
License: BSD-3-Clause

(Full license text not bundled in wheel; see project home page.)
```

---

## torchvision:0.29.0.dev20260814+cu130

**License Type:** BSD-3-Clause

**Source:** PyPI

```
BSD 3-Clause License

Copyright (c) Soumith Chintala 2016, 
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

---

## transformers:4.57.6

**License Type:** Apache 2.0 License

**Source:** PyPI

```
License: Apache 2.0 License
License :: OSI Approved :: Apache Software License
(Full license text not bundled in wheel; see project home page.)
```

---

## triton:3.8.0+git675c5987

**License Type:** License :: OSI Approved :: MIT License

**Source:** PyPI

```
License: 
License :: OSI Approved :: MIT License
(Full license text not bundled in wheel; see project home page.)
```

---

## urllib3:2.7.0

**License Type:** MIT

**Source:** PyPI

```
License: MIT

(Full license text not bundled in wheel; see project home page.)
```

---

## wcwidth:0.8.2

**License Type:** MIT

**Source:** PyPI

```
License: MIT

(Full license text not bundled in wheel; see project home page.)
```

---
