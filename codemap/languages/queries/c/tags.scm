;; codemap T1 tags for C. See languages/registry.py for the capture contract.

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name
    parameters: (parameter_list) @params)) @definition.function

;; --- references (call sites, by name) ------------------------------------

(call_expression function: (identifier) @name) @reference.call

;; --- imports (#include, raw text kept verbatim) ---------------------------

(preproc_include) @import
