;; codemap T1 tags for C++. See languages/registry.py for the capture contract.
;; A method inside a class body byte-range-nests inside its `class_specifier`,
;; so parsing.py's generic function->method promotion applies here, same as
;; Python/TS — no separate method pattern needed.

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name
    parameters: (parameter_list) @params)) @definition.function
(function_definition
  declarator: (function_declarator
    declarator: (field_identifier) @name
    parameters: (parameter_list) @params)) @definition.function

(class_specifier  name: (type_identifier) @name) @definition.class
(struct_specifier name: (type_identifier) @name) @definition.class

;; --- references (call sites, by name) ------------------------------------

(call_expression function: (identifier) @name) @reference.call
(call_expression
  function: (field_expression field: (field_identifier) @name)) @reference.call

;; --- imports ---------------------------------------------------------------

(preproc_include) @import
