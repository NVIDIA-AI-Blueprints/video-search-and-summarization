// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_LIFETIME_ANNOTATIONS_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_LIFETIME_ANNOTATIONS_H_

// Shorthand for arbitrary-length lifetime name annotations.
// This can be used like `$(my_lifetime_name)`.
//
// See also the predefined macros for `$static` and single-character
// (e.g. `$a`) lifetimes below.
//
// These annotations communicate the lifetimes of the input types to Crubit.
// For more information on the design, see b/454627672.
//
// TODO(mboehme): We would prefer `$(...)` to be a variadic macro that
// stringizes each of its macro arguments individually. This is possible but
// requires some contortions: https://stackoverflow.com/a/5958315
#define $(l) [[clang::annotate_type("lifetime", #l)]]

// Shorthand for a static lifetime annotation.
#define $static $(static)

// Shorthand for a single-character lifetime annotation.
#define $a $(a)
#define $b $(b)
#define $c $(c)
#define $d $(d)
#define $e $(e)
#define $f $(f)
#define $g $(g)
#define $h $(h)
#define $i $(i)
#define $j $(j)
#define $k $(k)
#define $l $(l)
#define $m $(m)
#define $n $(n)
#define $o $(o)
#define $p $(p)
#define $q $(q)
#define $r $(r)
#define $s $(s)
#define $t $(t)
#define $u $(u)
#define $v $(v)
#define $w $(w)
#define $x $(x)
#define $y $(y)
#define $z $(z)

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_LIFETIME_ANNOTATIONS_H_
