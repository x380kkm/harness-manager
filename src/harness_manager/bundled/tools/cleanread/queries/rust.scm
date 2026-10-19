;; audience: internal
;; rust
;; Rust 遍历器负责声明, 包含关系和 trait 实现.
;; 本查询从每个外围符号生成依赖边.

;; use 声明
(use_declaration argument: (identifier) @dep.imports)
(use_declaration argument: (scoped_identifier) @dep.imports)
(use_declaration
  argument: (scoped_use_list
    path: (scoped_identifier) @dep.imports))
(scoped_use_list
  list: (use_list (identifier) @dep.imports))

;; 函数和方法调用
(call_expression function: (identifier) @dep.calls)
(call_expression
  function: (field_expression field: (field_identifier) @dep.calls))
(call_expression
  function: (scoped_identifier name: (identifier) @dep.calls))

;; 具名类型位置
(parameter type: (type_identifier) @dep.uses_type)
(parameter type: (reference_type type: (type_identifier) @dep.uses_type))
(parameter type: (generic_type type: (type_identifier) @dep.uses_type))
(parameter
  type: (generic_type
    type_arguments: (type_arguments (type_identifier) @dep.uses_type)))
(function_item return_type: (type_identifier) @dep.uses_type)
(function_item return_type: (reference_type type: (type_identifier) @dep.uses_type))
(function_item return_type: (generic_type type: (type_identifier) @dep.uses_type))
(function_signature_item return_type: (type_identifier) @dep.uses_type)
(function_signature_item return_type: (reference_type type: (type_identifier) @dep.uses_type))
(function_signature_item return_type: (generic_type type: (type_identifier) @dep.uses_type))
(field_declaration type: (type_identifier) @dep.uses_type)
(field_declaration type: (reference_type type: (type_identifier) @dep.uses_type))
(field_declaration type: (generic_type type: (type_identifier) @dep.uses_type))
(const_item type: (type_identifier) @dep.uses_type)
(const_item type: (reference_type type: (type_identifier) @dep.uses_type))
(const_item type: (generic_type type: (type_identifier) @dep.uses_type))
(type_item type: (type_identifier) @dep.uses_type)
(type_item type: (reference_type type: (type_identifier) @dep.uses_type))
(type_item type: (generic_type type: (type_identifier) @dep.uses_type))
