/* Generated from GET /api/schema by scripts/gen-types.mjs. Do not edit; run `npm run gen:types`. */

export type SchemaVersion = "1.0";
export type RequestId = string;
export type ConversationId = string;
export type AnswerType = "answer" | "clarification" | "out_of_scope" | "error";
export type Category =
  | "nutrition"
  | "food_safety"
  | "general_food"
  | "mixed"
  | "out_of_scope"
  | "none";
export type Answer = string;
export type Text = string;
export type Source = null;
export type Claims = Claim[];
export type Notices = string[];

export interface ChatResponse {
  schema_version: SchemaVersion;
  request_id: RequestId;
  conversation_id: ConversationId;
  answer_type: AnswerType;
  category: Category;
  answer: Answer;
  claims: Claims;
  notices: Notices;
}
export interface Claim {
  text: Text;
  source: Source;
}
