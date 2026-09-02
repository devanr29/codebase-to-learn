;; codemap T1 tags for Ruby. See languages/registry.py for the capture contract.

;; --- definitions -------------------------------------------------------------
;; `method` covers both top-level `def` and class-nested `def` — parsing.py's
;; generic function->method promotion (via byte-range nesting inside a
;; captured `class`) does the rest, same as Python.

(method
  name: (identifier) @name
  parameters: (method_parameters)? @params) @definition.function

(singleton_method
  name: (identifier) @name
  parameters: (method_parameters)? @params) @definition.function

(class  name: (constant) @name) @definition.class
(module name: (constant) @name) @definition.class

;; --- references (call sites, by name) ------------------------------------

(call method: (identifier) @name) @reference.call

;; --- imports -----------------------------------------------------------
;; `require`/`require_relative`/`load` are themselves ordinary method calls
;; in Ruby's grammar (there is no import-statement node), so a matching call
;; also satisfies @reference.call above — harmless duplication: "require"
;; never resolves to a captured symbol, so impact.call_graph drops the ref.

((call
  method: (identifier) @_m
  arguments: (argument_list (string))) @import
  (#any-of? @_m "require" "require_relative" "load"))
