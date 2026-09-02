;; codemap T1 tags for Go. See languages/registry.py for the capture contract.

;; --- definitions -------------------------------------------------------------

(function_declaration
  name: (identifier) @name
  parameters: (parameter_list) @params
  result: (_)? @returns) @definition.function

;; Go methods are declared via a receiver, not nested inside their type, so the
;; grammar's own node type — not byte-range nesting — is what makes this a
;; method: captured directly rather than via parsing.py's generic
;; function->method promotion. Qualified names are therefore flat (the
;; receiver type is not prefixed) — a known simplification.
(method_declaration
  name: (field_identifier) @name
  parameters: (parameter_list) @params
  result: (_)? @returns) @definition.method

(type_declaration
  (type_spec
    name: (type_identifier) @name
    type: (struct_type)) @definition.class)

(type_declaration
  (type_spec
    name: (type_identifier) @name
    type: (interface_type)) @definition.interface)

;; --- references (call sites, by name) ------------------------------------

(call_expression function: (identifier) @name) @reference.call
(call_expression
  function: (selector_expression field: (field_identifier) @name)) @reference.call

;; --- imports (one @import per spec — covers both `import "x"` and the
;; grouped `import ( "x" \n "y" )` form, since both use import_spec) ---------

(import_spec) @import
