;; codemap T1 tags for TypeScript / TSX. See languages/registry.py for the contract.

;; --- definitions -------------------------------------------------------------

(function_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params
  return_type: (type_annotation)? @returns) @definition.function

(generator_function_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params) @definition.function

;; arrow / function expression bound to a name
(variable_declarator
  name: (identifier) @name
  value: (arrow_function parameters: (formal_parameters) @params)) @definition.function
(variable_declarator
  name: (identifier) @name
  value: (function_expression parameters: (formal_parameters) @params)) @definition.function

(method_definition
  name: (property_identifier) @name
  parameters: (formal_parameters) @params
  return_type: (type_annotation)? @returns) @definition.method

(class_declaration          name: (type_identifier) @name) @definition.class
(abstract_class_declaration name: (type_identifier) @name) @definition.class

(interface_declaration  name: (type_identifier) @name) @definition.interface
(type_alias_declaration name: (type_identifier) @name) @definition.type

;; --- decorators ------------------------------------------------------------

(decorator) @decorator

;; --- references (call sites, by name) ------------------------------------

(call_expression function: (identifier) @name) @reference.call
(call_expression
  function: (member_expression property: (property_identifier) @name)) @reference.call

;; --- imports -----------------------------------------------------------------

(import_statement) @import
(export_statement source: (string)) @import
