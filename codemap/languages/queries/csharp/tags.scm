;; codemap T1 tags for C#. See languages/registry.py for the capture contract.

;; --- definitions -------------------------------------------------------------

(method_declaration
  name: (identifier) @name
  parameters: (parameter_list) @params
  returns: (_)? @returns) @definition.method

(constructor_declaration
  name: (identifier) @name
  parameters: (parameter_list) @params) @definition.method

(class_declaration     name: (identifier) @name) @definition.class
(interface_declaration name: (identifier) @name) @definition.interface
(struct_declaration    name: (identifier) @name) @definition.class
(record_declaration    name: (identifier) @name) @definition.class

;; --- references (call sites, by name) ------------------------------------

(invocation_expression function: (identifier) @name) @reference.call
(invocation_expression
  function: (member_access_expression name: (identifier) @name)) @reference.call

;; --- imports ---------------------------------------------------------------

(using_directive) @import
