import { Hero } from "@/components/home/Hero";
import { ModelEdges } from "@/components/home/ModelEdges";
import { WeekGames } from "@/components/home/WeekGames";
import { TrackRecordTeaser } from "@/components/home/TrackRecordTeaser";
import { NumbyTeaser } from "@/components/home/NumbyTeaser";
import { ModelWorksTeaser } from "@/components/home/ModelWorksTeaser";

export default function HomePage() {
  return (
    <>
      <Hero />
      <ModelEdges />
      <WeekGames />
      <TrackRecordTeaser />
      <NumbyTeaser />
      <ModelWorksTeaser />
    </>
  );
}
