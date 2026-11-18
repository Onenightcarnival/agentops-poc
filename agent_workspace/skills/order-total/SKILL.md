---
name: order-total
metadata:
  id: agentops.order-total
  version: "1.0.0"
description: Review order quantities and calculate a small order total, reporting CNY with two decimal places.
---

Follow this workflow for the original order supplied by the user:

1. Check that items is a nonempty list and each item has unit_price and quantity.
2. Check that unit_price is a finite, nonnegative decimal value, supplied either as
   a JSON number or a decimal string such as "12.50". Both representations are
   valid. Check that quantity is an integer from 1 through 10000. Boolean values
   are not quantities.
3. If a value is invalid, identify the field and explain the problem. For an invalid
   quantity, include the field name `quantity` in your answer. Do not repair inputs
   or continue calculating with invented values.
4. For valid small orders, multiply each unit price by its quantity, add the line
   amounts, and report the total with two decimal places and the CNY currency.
5. Keep any order identifier in the conversation for follow-up questions.

This is an instruction-based demo skill. Follow the steps directly; there is no
separate skill dispatcher or executable tool required by this skill. Do not claim
that a program validated or calculated the order.
