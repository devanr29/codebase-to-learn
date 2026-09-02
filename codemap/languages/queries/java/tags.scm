;; codemap T1 tags for Java. See languages/registry.py for the capture contract.

;; --- definitions -------------------------------------------------------------

(method_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params
  type: (_)? @returns) @definition.method

(constructor_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params) @definition.method

(class_declaration     name: (identifier) @name) @definition.class
(interface_declaration name: (identifier) @name) @definition.interface
(enum_declaration      name: (identifier) @name) @definition.class
(record_declaration    name: (identifier) @name) @definition.class

;; --- references (call sites, by name) ------------------------------------

(method_invocation name: (identifier) @name) @reference.call

;; --- imports ---------------------------------------------------------------

(import_declaration) @import
