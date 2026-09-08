;; codemap T1 tags for TSX. See languages/registry.py for the contract.
;; Identical to typescript/tags.scm plus the JSX component-reference rules at
;; the bottom — kept as a separate file (not shared) because the plain
;; `typescript` grammar has no JSX node types and fails to *compile* a query
;; that references them.

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

;; --- JSX component references (capitalized names only; filtered in
;;     parsing.py — intrinsic host elements like <div> aren't components) --

(jsx_opening_element      name: (identifier) @name) @reference.render
(jsx_self_closing_element name: (identifier) @name) @reference.render
(jsx_opening_element
  name: (member_expression property: (property_identifier) @name)) @reference.render
(jsx_self_closing_element
  name: (member_expression property: (property_identifier) @name)) @reference.render

;; --- imports -----------------------------------------------------------------

(import_statement) @import
(export_statement source: (string)) @import
