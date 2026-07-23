import { Check } from 'lucide-react';

export function Stepper({ steps, currentStep, onStepClick }) {
  return (
    <div className="stepper">
      {steps.map((label, index) => {
        const isCompleted = index < currentStep;
        const isActive = index === currentStep;
        const isUpcoming = index > currentStep;

        return (
          <div
            key={label}
            className={`stepper-item${isCompleted ? ' completed' : ''}${isActive ? ' active' : ''}${isUpcoming ? ' upcoming' : ''}`}
          >
            {index > 0 && (
              <div className={`stepper-line${index <= currentStep ? ' filled' : ''}`} />
            )}
            <button
              type="button"
              className="stepper-circle"
              onClick={() => onStepClick?.(index)}
              disabled={isUpcoming}
              aria-label={`Step ${index + 1}: ${label}`}
            >
              {isCompleted ? <Check size={14} strokeWidth={3} /> : index + 1}
            </button>
            <span className="stepper-label">{label}</span>
          </div>
        );
      })}
    </div>
  );
}
