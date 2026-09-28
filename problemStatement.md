# Problem Statement

## AI-Powered Food, Nutrition & Food Safety Chatbot

Build a chatbot that answers user questions about food, nutrition, and food safety. The system should use an LLM to understand natural-language questions and give clear, relevant, and easy-to-understand answers.

---

## Objective

Design and build an application that:

- Takes questions from users about food, nutrition, and food safety
- Uses an LLM to understand the user's question and generate a response
- Provides relevant, understandable, and context-aware information
- Clearly distinguishes between **general food/nutrition information** and **food-safety-related questions**
- Displays the response in a simple conversational interface

---

## System Workflow

```
User Input → Question Understanding → Knowledge & Context Layer → LLM Integration Layer → Response Generation → Output Display
```

### 1. User Input

Accept a natural-language question from the user.

Examples:

- "Is brown rice healthier than white rice?"
- "How much protein is there in 100g of paneer?"
- "Can I eat cooked rice that was left outside overnight?"
- "What foods are high in iron?"
- "How long can chicken be stored in the refrigerator?"

### 2. Question Understanding

Analyze the user's question to determine:

- What the user is asking
- Whether the question relates to food, nutrition, or food safety
- Relevant entities such as food items, nutrients, quantities, storage conditions, or cooking methods
- Any important context provided by the user

### 3. Knowledge & Context Layer

Prepare the information needed to answer the question.

Where available, the system should give the LLM relevant food, nutrition, or food-safety information as context.

### 4. LLM Integration Layer

Pass the user's question and the relevant context to the LLM.

Design a prompt that instructs the LLM to:

- Answer the user's specific question
- Use the provided context where applicable
- Explain the answer clearly
- Avoid making unsupported claims
- Ask for clarification when the question does not contain enough information

### 5. Response Generation

The LLM generates a response based on the user's question and the available context.

The response may include:

- Direct answer
- Explanation
- Relevant nutritional information
- Food-safety considerations
- Practical guidance
- Clarifying questions when required

### 6. Output Display

Display the generated response in a conversational chatbot interface.

The response should be:

- Clear and easy to understand
- Relevant to the user's question
- Structured where useful, using bullets or short sections
- Explicit about uncertainty when there isn't enough information
