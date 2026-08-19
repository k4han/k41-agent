import { createSignal, Show } from "solid-js";
import { SettingsLayout } from "./SettingsLayout";

// Import new form components
import {
  FormField,
  FormFieldGroup,
  FormInput,
  FormTextarea,
  FormSelect,
  FormButton,
  FormValidation,
  FormSuccess,
  FormGrid,
  FormSection,
  FormCard,
  FormActions,
  type ValidationRule,
  type ValidationMessage,
} from "@/components/forms";

export function FormDemo() {
  const [name, setName] = createSignal("");
  const [email, setEmail] = createSignal("");
  const [provider, setProvider] = createSignal("");
  const [description, setDescription] = createSignal("");
  const [loading, setLoading] = createSignal(false);
  const [success, setSuccess] = createSignal(false);
  const [validationMessages, setValidationMessages] = createSignal<ValidationMessage[]>([]);

  const emailValidation: ValidationRule = {
    validate: (value) => {
      if (!value) return true;
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      return emailRegex.test(value) || "Please enter a valid email address";
    },
    message: "Invalid email format",
  };

  const nameValidation: ValidationRule = {
    validate: (value) => {
      if (!value) return true;
      return value.length >= 2 || "Name must be at least 2 characters";
    },
    message: "Name too short",
  };

  const providerOptions = [
    { value: "openai", label: "OpenAI" },
    { value: "anthropic", label: "Anthropic" },
    { value: "google", label: "Google" },
    { value: "custom", label: "Custom Provider" },
  ];

  const handleSubmit = async () => {
    setValidationMessages([]);

    // Manual validation
    const errors: ValidationMessage[] = [];

    if (!name()) {
      errors.push({ type: "error", message: "Name is required", field: "name" });
    }

    if (!email()) {
      errors.push({ type: "error", message: "Email is required", field: "email" });
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email())) {
      errors.push({ type: "error", message: "Invalid email format", field: "email" });
    }

    if (!provider()) {
      errors.push({ type: "error", message: "Provider is required", field: "provider" });
    }

    if (errors.length > 0) {
      setValidationMessages(errors);
      return;
    }

    setLoading(true);
    try {
      // Simulate API call
      await new Promise((resolve) => setTimeout(resolve, 1500));
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (error) {
      setValidationMessages([
        { type: "error", message: "Failed to save settings" },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setName("");
    setEmail("");
    setProvider("");
    setDescription("");
    setValidationMessages([]);
    setSuccess(false);
  };

  return (
    <SettingsLayout
      title="Form UX Demo"
      breadcrumbLabel="Form Demo"
      contentWidth="medium"
    >
      <FormSuccess
        show={success()}
        message="Settings saved successfully!"
        onDismiss={() => setSuccess(false)}
      />

      <FormValidation
        messages={validationMessages()}
        showSummary
        onDismiss={(index) => {
          setValidationMessages((prev) => prev.filter((_, i) => i !== index));
        }}
      />

      <FormCard
        title="Provider Configuration"
        description="Configure your AI provider settings with improved form UX"
        variant="elevated"
      >
        <FormGrid columns={2}>
          <FormField
            label="Provider Name"
            required
            error={validationMessages().find((m) => m.field === "name")?.message}
            helper="Choose a descriptive name for your provider"
          >
            <FormInput
              value={name()}
              onChange={setName}
              placeholder="My Provider"
              required
              validation={[nameValidation]}
              showValidationStatus
            />
          </FormField>

          <FormField
            label="Email Contact"
            required
            error={validationMessages().find((m) => m.field === "email")?.message}
            helper="For notifications and support"
          >
            <FormInput
              value={email()}
              onChange={setEmail}
              type="email"
              placeholder="admin@example.com"
              required
              validation={[emailValidation]}
              showValidationStatus
            />
          </FormField>
        </FormGrid>

        <FormField
          label="Provider Type"
          required
          error={validationMessages().find((m) => m.field === "provider")?.message}
        >
          <FormSelect
            value={provider()}
            onChange={setProvider}
            options={providerOptions}
            placeholder="Select a provider"
            required
            showValidationStatus
          />
        </FormField>

        <FormField
          label="Description"
          helper="Optional description for this provider"
        >
          <FormTextarea
            value={description()}
            onChange={setDescription}
            placeholder="Add a description..."
            rows={3}
            maxLength={500}
          />
        </FormField>

        <FormActions align="right">
          <FormButton
            variant="secondary"
            onClick={handleReset}
            disabled={loading()}
          >
            Reset
          </FormButton>
          <FormButton
            variant="primary"
            onClick={handleSubmit}
            loading={loading()}
            loadingText="Saving..."
          >
            Save Settings
          </FormButton>
        </FormActions>
      </FormCard>

      <FormSection
        title="Advanced Settings"
        description="Additional configuration options"
        collapsible
        defaultCollapsed
      >
        <FormFieldGroup title="API Configuration">
          <FormField
            label="API Endpoint"
            helper="Custom API endpoint (optional)"
          >
            <FormInput
              value=""
              onChange={() => {}}
              placeholder="https://api.example.com"
            />
          </FormField>

          <FormField
            label="API Key"
            helper="Enter your API key for authentication"
          >
            <FormInput
              value=""
              onChange={() => {}}
              type="password"
              placeholder="Enter your API key"
              showTogglePassword
            />
          </FormField>
        </FormFieldGroup>

        <FormFieldGroup title="Rate Limiting">
          <FormGrid columns={2}>
            <FormField
              label="Requests per minute"
              helper="Maximum API requests per minute"
            >
              <FormInput
                value=""
                onChange={() => {}}
                type="number"
                placeholder="60"
              />
            </FormField>

            <FormField
              label="Timeout (seconds)"
              helper="Request timeout in seconds"
            >
              <FormInput
                value=""
                onChange={() => {}}
                type="number"
                placeholder="30"
              />
            </FormField>
          </FormGrid>
        </FormFieldGroup>
      </FormSection>
    </SettingsLayout>
  );
}
