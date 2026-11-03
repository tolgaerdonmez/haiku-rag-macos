import { LaunchProps } from "@raycast/api";
import { SearchView } from "./components/SearchView";

export default function Command(props: LaunchProps) {
  return <SearchView initialQuery={props.fallbackText ?? ""} />;
}
