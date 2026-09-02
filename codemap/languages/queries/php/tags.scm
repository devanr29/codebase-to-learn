;; codemap T1 tags for PHP. See languages/registry.py for the capture contract.

;; --- definitions -------------------------------------------------------------

(method_declaration
  name: (name) @name
  parameters: (formal_parameters) @params) @definition.method

(function_definition
  name: (name) @name
  parameters: (formal_parameters) @params) @definition.function

(class_declaration     name: (name) @name) @definition.class
(interface_declaration name: (name) @name) @definition.interface
(trait_declaration     name: (name) @name) @definition.interface

;; --- references (call sites, by name) ------------------------------------

(function_call_expression function: (name) @name) @reference.call
(member_call_expression   name: (name) @name) @reference.call

;; --- imports ---------------------------------------------------------------

(namespace_use_declaration) @import
